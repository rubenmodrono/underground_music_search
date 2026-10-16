#!/usr/bin/env python3
"""Buscador de joyas underground en Bandcamp (y Hypeddit, best-effort).

Solo usa la libreria estandar. Pensado para ejecutarse en TU ordenador:
desde el entorno cloud donde se escribio, Bandcamp/Hypeddit estaban bloqueados,
asi que NO esta probado contra los sitios reales.

Uso:
  python3 musicsearch.py bandcamp -t "hypnotic techno" -t "raw techno" --max-listeners 5000 --pages 3
  python3 musicsearch.py bandcamp -t "hard groove" --since-days 14 --csv out.csv
  python3 musicsearch.py hypeddit --pages 1
"""
import argparse, csv, json, re, sys, time, urllib.request, html
from datetime import datetime, timedelta, timezone

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
DIG = "https://bandcamp.com/api/hub/2/dig_deeper"

def http(url, data=None, headers=None):
    h = {"User-Agent": UA, "Accept": "application/json, text/html"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")

def bandcamp(tags, sort, pages, fmt, geoname):
    """Usa el endpoint JSON interno que alimenta bandcamp.com/tag/<x>."""
    out, page = [], 1
    for page in range(1, pages + 1):
        body = json.dumps({"filters": {"format": fmt, "location": geoname, "sort": sort,
                                       "tags": tags}, "page": page}).encode()
        try:
            j = json.loads(http(DIG, body, {"Content-Type": "application/json"}))
        except Exception as e:
            print(f"[bandcamp] pagina {page} fallo: {e}", file=sys.stderr)
            if page == 1: raise RuntimeError(f"Bandcamp no responde ({e})")
            break
        items = j.get("items") or []
        if not items: break
        for it in items:
            out.append({
                "source": "bandcamp",
                "artist": it.get("artist"), "title": it.get("title"),
                "label": it.get("band_name") or it.get("artist"),
                "url": it.get("tralbum_url"),
                "genre": it.get("genre"), "tags": ",".join(tags),
                "country": it.get("country") or it.get("location"),
                "released": it.get("release_date") or it.get("publish_date"),
                "price": it.get("price"), "listeners": None,
            })
        if not j.get("more_available"): break
        time.sleep(1.0)
    return out

def enrich(item):
    """Abre la pagina del album y saca fecha, precio y nº de oyentes/compras si aparece."""
    try:
        h = http(item["url"])
    except Exception as e:
        item["note"] = f"no se pudo abrir: {e}"; return
    m = re.search(r'data-tralbum="([^"]+)"', h)
    if m:
        d = json.loads(html.unescape(m.group(1)))
        cur = d.get("current") or {}
        item["released"] = d.get("album_release_date") or cur.get("release_date") or item["released"]
        item["price"] = (d.get("trackinfo") or [{}])[0].get("price") or cur.get("minimum_price")
        item["free"] = bool(d.get("freeDownloadPage")) or cur.get("minimum_price") == 0
        try: item["preview"] = d["trackinfo"][0]["file"]["mp3-128"]
        except Exception: pass
        item["art_title"] = cur.get("title")
    # numero de coleccionistas: "supported by" / thumbs de fans
    item["supporters"] = len(re.findall(r'class="collected-by', h)) or len(re.findall(r'class="fan-', h)) or None
    # tag "n sold"/"oyentes" no existe publicamente; usamos seguidores del artista como proxy
    m = re.search(r'(\d[\d,\.]*)\s+(?:followers|seguidores)', h, re.I)
    item["listeners"] = int(re.sub(r"[^\d]", "", m.group(1))) if m else None

def parse_date(s):
    for f in ("%d %b %Y %H:%M:%S GMT", "%Y-%m-%d", "%d %b %Y"):
        try: return datetime.strptime(s, f).replace(tzinfo=timezone.utc)
        except Exception: pass

def hypeddit(pages):
    """Best-effort: lee /newreleases y devuelve enlaces /track/<id> con su <title>."""
    out = []
    for path in ("newreleases/downloads", "newreleases/streams", "newreleases"):
        try: h = http("https://hypeddit.com/" + path)
        except Exception as e:
            print(f"[hypeddit] {path}: {e}", file=sys.stderr); continue
        for tid in dict.fromkeys(re.findall(r'hypeddit\.com/track/([a-z0-9]+)|href="/track/([a-z0-9]+)"', h) and
                                 [a or b for a, b in re.findall(r'hypeddit\.com/track/([a-z0-9]+)|href="/track/([a-z0-9]+)"', h)]):
            url = f"https://hypeddit.com/track/{tid}"
            title = artist = None
            try:
                t = http(url); m = re.search(r"<title>(.*?)</title>", t, re.S)
                title = html.unescape(m.group(1)).strip() if m else None
                time.sleep(0.7)
            except Exception: pass
            out.append({"source": "hypeddit", "artist": artist, "title": title, "label": None, "url": url,
                        "genre": None, "tags": path, "country": None, "released": None, "price": "free?", "listeners": None})
    return out

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("bandcamp")
    b.add_argument("-t", "--tag", action="append", required=True, help="etiqueta (repetible: se combinan)")
    b.add_argument("--sort", default="date", choices=["date", "pop", "random"], help="date=novedades, pop=populares")
    b.add_argument("--pages", type=int, default=2)
    b.add_argument("--format", default="all", help="all|digital|vinyl|cd|cassette")
    b.add_argument("--since-days", type=int, help="solo lanzamientos de los ultimos N dias")
    b.add_argument("--max-listeners", type=int, help="descarta artistas con mas seguidores (abre cada pagina; lento)")
    b.add_argument("--free", action="store_true", help="solo gratis / name-your-price")
    h = sp.add_parser("hypeddit"); h.add_argument("--pages", type=int, default=1)
    for p in (b, h):
        p.add_argument("--csv"); p.add_argument("--json")
    a = ap.parse_args()

    if a.cmd == "bandcamp":
        items = bandcamp(a.tag, a.sort, a.pages, a.format, 0)
        if a.max_listeners or a.free or a.since_days:
            for i in items: enrich(i); time.sleep(0.8)
        if a.since_days:
            lim = datetime.now(timezone.utc) - timedelta(days=a.since_days)
            items = [i for i in items if (parse_date(i["released"] or "") or lim) >= lim]
        if a.max_listeners:
            items = [i for i in items if i.get("listeners") is None or i["listeners"] <= a.max_listeners]
        if a.free:
            items = [i for i in items if i.get("free")]
    else:
        items = hypeddit(a.pages)

    for i in items:
        print(f"[{i['source']}] {i['artist'] or ''} - {i['title'] or ''} | {i['genre'] or ''} | {i['url']}")
    print(f"\n{len(items)} resultados", file=sys.stderr)
    if a.csv and items:
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=sorted({k for i in items for k in i})); w.writeheader(); w.writerows(items)
    if a.json:
        json.dump(items, open(a.json, "w"), ensure_ascii=False, indent=1)

if __name__ == "__main__":
    main()
