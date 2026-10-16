#!/usr/bin/env python3
"""Interfaz web + agentes + auditor que aprende. Solo libreria estandar.

  python3 app.py serve [--port 8080]     # web en http://localhost:8080
  python3 app.py run-daily               # ejecuta todas las busquedas (cron a las 10:00)
  python3 app.py daemon                  # servidor + planificador interno (10:00 hora local)
  python3 app.py seed-demo               # datos falsos para probar la interfaz sin red
Cron alternativo:  0 10 * * *  cd /ruta/music-search && python3 app.py run-daily
"""
import sqlite3, json, math, re, sys, time, threading, html, os, secrets
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import musicsearch as ms

DB = os.environ.get("MUSIC_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "music.db"))
MIN_DECISIONS = 20     # antes de esto el auditor no descarta por gusto, solo ordena
THRESHOLD = 0.25       # por debajo -> descarte automatico (tras el arranque en frio)
LR = 0.3

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT UNIQUE);
    CREATE TABLE IF NOT EXISTS searches(id INTEGER PRIMARY KEY, user_id INT, site TEXT, tags TEXT,
        sort TEXT DEFAULT 'date', since_days INT, max_listeners INT, free_only INT DEFAULT 0, active INT DEFAULT 1);
    CREATE TABLE IF NOT EXISTS candidates(id INTEGER PRIMARY KEY, user_id INT, search_id INT, url TEXT,
        data TEXT, score REAL, status TEXT, found TEXT, UNIQUE(user_id,url));
    -- status: auto_rejected | pending (aceptado por auditor, espera al usuario) | kept | rejected
    CREATE TABLE IF NOT EXISTS weights(user_id INT, feat TEXT, w REAL, PRIMARY KEY(user_id,feat));
    """); return c

# ---------- auditor ----------
def features(it):
    f = set()
    for k in ("artist", "label", "genre", "country", "source"):
        if it.get(k): f.add(f"{k}:{str(it[k]).lower().strip()}")
    for t in (it.get("tags") or "").split(","):
        if t.strip(): f.add("tag:" + t.strip().lower())
    for w in re.findall(r"[a-záéíóúñ]{4,}", (it.get("title") or "").lower()): f.add("w:" + w)
    if it.get("free"): f.add("free")
    return f

def score(c, uid, it):
    ws = {r["feat"]: r["w"] for r in c.execute("SELECT feat,w FROM weights WHERE user_id=?", (uid,))}
    z = sum(ws.get(x, 0) for x in features(it))
    return 1 / (1 + math.exp(-z)) if z else 0.5

def learn(c, uid, it, keep):
    """Un paso de regresion logistica (SGD): conserva=1, descarta=0."""
    p = score(c, uid, it); g = (1 if keep else 0) - p
    for x in features(it):
        c.execute("INSERT INTO weights VALUES(?,?,?) ON CONFLICT(user_id,feat) DO UPDATE SET w=w+?", (uid, x, LR * g, LR * g))

def n_decisions(c, uid):
    return c.execute("SELECT COUNT(*) FROM candidates WHERE user_id=? AND status IN('kept','rejected')", (uid,)).fetchone()[0]

RUNS = {}  # search_id -> texto de estado ("ejecutando…", resultado o error)

def run_bg(s):
    sid = s["id"]; RUNS[sid] = "⏳ Ejecutando… (puede tardar varios minutos)"
    try:
        new, acc = run_search(db(), s)
        RUNS[sid] = f"✔ {datetime.now():%H:%M} · {new} nuevos, {acc} pasan al auditor" + ("" if new else " (0 resultados: revisa los tags o la conexión)")
    except Exception as e:
        RUNS[sid] = f"✖ {datetime.now():%H:%M} · falló: {e}"

# ---------- agentes de sitio + auditoria ----------
def run_search(c, s):
    tags = [t.strip() for t in s["tags"].split(",") if t.strip()]
    if s["site"] == "bandcamp":
        items = ms.bandcamp(tags, s["sort"], 2, "all", 0)
        for i in items: ms.enrich(i); time.sleep(0.8)
    else:
        items = ms.hypeddit(1)
    cold = n_decisions(c, s["user_id"]) < MIN_DECISIONS
    new = acc = 0
    for it in items:
        if c.execute("SELECT 1 FROM candidates WHERE user_id=? AND url=?", (s["user_id"], it["url"])).fetchone(): continue
        new += 1
        hard_ok = not (s["max_listeners"] and it.get("listeners") and it["listeners"] > s["max_listeners"]) \
                  and not (s["free_only"] and not it.get("free"))
        if s["since_days"] and it.get("released"):
            d = ms.parse_date(it["released"])
            if d and (datetime.now(d.tzinfo) - d).days > s["since_days"]: hard_ok = False
        sc = score(c, s["user_id"], it)
        ok = hard_ok and (cold or sc >= THRESHOLD)
        acc += ok
        c.execute("INSERT INTO candidates(user_id,search_id,url,data,score,status,found) VALUES(?,?,?,?,?,?,?)",
                  (s["user_id"], s["id"], it["url"], json.dumps(it), sc, "pending" if ok else "auto_rejected", datetime.now().isoformat(timespec="minutes")))
    c.commit(); return new, acc

def run_daily():
    c = db()
    for s in c.execute("SELECT * FROM searches WHERE active=1").fetchall():
        try: print("search", s["id"], s["site"], s["tags"], run_search(c, s))
        except Exception as e: print("search", s["id"], "fallo:", e, file=sys.stderr)

def scheduler(hour=10):
    last = None
    while True:
        n = datetime.now()
        if n.hour == hour and last != n.date(): last = n.date(); run_daily()
        time.sleep(30)

# ---------- web ----------
CSS = """body{font:15px system-ui;max-width:860px;margin:0 auto;padding:16px;background:#111;color:#ddd}
a{color:#8cf}input,select,button{font:inherit;padding:6px 8px;margin:2px;background:#222;color:#ddd;border:1px solid #444;border-radius:4px}
.card{border:1px solid #333;border-radius:8px;padding:12px;margin:10px 0}.mut{color:#888;font-size:13px}
button.k{background:#164}button.r{background:#611}audio{width:100%;margin-top:6px}iframe{width:100%;border:0;height:90px}"""
def page(body, user=None):
    nav = f"<p>{'<b>'+html.escape(user)+'</b> · <a href=/>Pendientes</a> · <a href=/searches>Búsquedas</a> · <a href=/kept>Guardados</a> · <a href=/taste>Gustos</a> · <a href=/logout>Salir</a>' if user else ''}</p>"
    return f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Music Search</title><style>{CSS}</style><h2>Music Search</h2>{nav}{body}".encode()

def preview(it):
    if it.get("preview"): return f"<audio controls preload=none src='{html.escape(it['preview'])}'></audio>"
    m = re.search(r"/track/", it["url"]) and it["source"] == "hypeddit"
    return f"<p class=mut>Sin preview directo · <a href='{html.escape(it['url'])}' target=_blank>abrir</a></p>"

def card(r, buttons=True):
    it = json.loads(r["data"]); e = html.escape
    b = f"<form method=post action=/decide><input type=hidden name=id value={r['id']}><button class=k name=a value=keep>Guardar</button><button class=r name=a value=reject>Descartar</button></form>" if buttons else ""
    return (f"<div class=card><b>{e(it.get('artist') or '?')}</b> — {e(it.get('title') or '?')}"
            f"<div class=mut>{e(it['source'])} · {e(str(it.get('genre') or ''))} · {e(str(it.get('tags') or ''))} · afinidad {r['score']:.2f} · <a href='{e(it['url'])}' target=_blank>abrir</a></div>{preview(it)}{b}</div>")

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def user(self, c):
        ck = self.headers.get("Cookie", ""); m = re.search(r"u=(\d+)", ck)
        return c.execute("SELECT * FROM users WHERE id=?", (m.group(1),)).fetchone() if m else None
    def send(self, body, code=200, extra=()):
        self.send_response(code); self.send_header("Content-Type", "text/html; charset=utf-8")
        for h in extra: self.send_header(*h)
        self.end_headers(); self.wfile.write(body)
    def redir(self, to, extra=()):
        self.send_response(303); self.send_header("Location", to)
        for h in extra: self.send_header(*h)
        self.end_headers()
    def do_GET(self):
        c = db(); u = self.user(c); p = urlparse(self.path).path
        if p == "/logout": return self.redir("/", [("Set-Cookie", "u=; Max-Age=0; Path=/")])
        if not u:
            return self.send(page("<form method=post action=/login>Tu nombre: <input name=name required><button>Entrar</button></form><p class=mut>Sin contraseña: pensado para uso personal/red local.</p>"))
        uid = u["id"]
        if p == "/":
            rows = c.execute("SELECT * FROM candidates WHERE user_id=? AND status='pending' ORDER BY score DESC", (uid,)).fetchall()
            body = f"<p class=mut>{len(rows)} aceptados por el auditor esperando tu decisión. Decisiones totales: {n_decisions(c,uid)} (el auditor empieza a descartar solo tras {MIN_DECISIONS}).</p>" + "".join(map(card, rows)) if rows else "<p>Nada pendiente. Crea una búsqueda y pulsa «Ejecutar ahora».</p>"
        elif p == "/kept":
            body = "".join(card(r, False) for r in c.execute("SELECT * FROM candidates WHERE user_id=? AND status='kept' ORDER BY id DESC", (uid,)))
        elif p == "/searches":
            rows = c.execute("SELECT * FROM searches WHERE user_id=?", (uid,)).fetchall()
            body = "<form method=post action=/search/add><select name=site><option>bandcamp<option>hypeddit</select> Tags: <input name=tags placeholder='hypnotic techno, raw techno'> Orden: <select name=sort><option>date<option>pop<option>random</select><br>Últimos <input name=since_days size=3> días · Máx seguidores <input name=max_listeners size=6> <label><input type=checkbox name=free_only value=1> solo gratis</label> <button>Añadir</button></form>"
            for s in rows:
                body += f"<div class=card>{s['site']} · <b>{html.escape(s['tags'])}</b> · {s['sort']} · {s['since_days'] or '-'}d · ≤{s['max_listeners'] or '-'} seg.<div class=mut>{html.escape(RUNS.get(s['id'], 'Sin ejecuciones desde que arrancó el servidor'))}</div><form method=post action=/search/run style=display:inline><input type=hidden name=id value={s['id']}><button>Ejecutar ahora</button></form><form method=post action=/search/del style=display:inline><input type=hidden name=id value={s['id']}><button class=r>Borrar</button></form></div>"
            if any("Ejecutando" in v for v in RUNS.values()): body += "<meta http-equiv=refresh content=5>"
            body += "<p class=mut>Se ejecutan solas cada día a las 10:00 (modo daemon o cron).</p>"
        elif p == "/taste":
            ws = c.execute("SELECT feat,w FROM weights WHERE user_id=? ORDER BY w DESC", (uid,)).fetchall()
            li = lambda rs: "".join(f"<li>{html.escape(r['feat'])} <span class=mut>{r['w']:+.2f}</span>" for r in rs)
            body = f"<h3>Lo que te gusta</h3><ul>{li(ws[:15])}</ul><h3>Lo que descartas</h3><ul>{li(ws[::-1][:15])}</ul>"
        else: return self.send(page("404", u["name"]), 404)
        self.send(page(body, u["name"]))
    def do_POST(self):
        c = db(); n = int(self.headers.get("Content-Length", 0))
        f = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
        if self.path == "/login":
            name = f.get("name", "").strip()[:40]
            c.execute("INSERT OR IGNORE INTO users(name) VALUES(?)", (name,)); c.commit()
            uid = c.execute("SELECT id FROM users WHERE name=?", (name,)).fetchone()[0]
            return self.redir("/", [("Set-Cookie", f"u={uid}; Path=/; HttpOnly; SameSite=Lax")])
        u = self.user(c)
        if not u: return self.redir("/")
        uid = u["id"]
        if self.path == "/decide":
            r = c.execute("SELECT * FROM candidates WHERE id=? AND user_id=?", (f.get("id"), uid)).fetchone()
            if r and r["status"] == "pending":
                keep = f.get("a") == "keep"; learn(c, uid, json.loads(r["data"]), keep)
                c.execute("UPDATE candidates SET status=? WHERE id=?", ("kept" if keep else "rejected", r["id"])); c.commit()
            return self.redir("/")
        if self.path == "/search/add":
            ti = lambda k: int(f[k]) if f.get(k, "").isdigit() else None
            c.execute("INSERT INTO searches(user_id,site,tags,sort,since_days,max_listeners,free_only) VALUES(?,?,?,?,?,?,?)",
                      (uid, f["site"], f.get("tags", ""), f.get("sort", "date"), ti("since_days"), ti("max_listeners"), 1 if f.get("free_only") else 0)); c.commit()
        elif self.path == "/search/del":
            c.execute("DELETE FROM searches WHERE id=? AND user_id=?", (f.get("id"), uid)); c.commit()
        elif self.path == "/search/run":
            s = c.execute("SELECT * FROM searches WHERE id=? AND user_id=?", (f.get("id"), uid)).fetchone()
            if s and "Ejecutando" not in RUNS.get(s["id"], ""): threading.Thread(target=run_bg, args=(s,), daemon=True).start()
            return self.redir("/searches")
        self.redir("/searches")

def serve(port, host="127.0.0.1"):
    print(f"http://{host}:{port}"); ThreadingHTTPServer((host, port), H).serve_forever()

def seed_demo():
    c = db(); c.execute("INSERT OR IGNORE INTO users(name) VALUES('demo')"); uid = c.execute("SELECT id FROM users WHERE name='demo'").fetchone()[0]
    c.execute("INSERT INTO searches(user_id,site,tags) VALUES(?,?,?)", (uid, "bandcamp", "hypnotic techno, raw techno"))
    for i, (a, t, g) in enumerate([("Demo A", "Dark Loop", "techno"), ("Demo B", "Soft Pad", "house"), ("Demo C", "Raw Machine", "techno")]):
        it = {"source": "bandcamp", "artist": a, "title": t, "genre": g, "tags": "raw techno", "url": f"https://example.com/{i}", "label": "Demo Label"}
        c.execute("INSERT OR IGNORE INTO candidates(user_id,search_id,url,data,score,status,found) VALUES(?,?,?,?,?,?,?)", (uid, 1, it["url"], json.dumps(it), score(c, uid, it), "pending", "now"))
    c.commit()

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8080
    if cmd == "serve": serve(port)
    elif cmd == "run-daily": run_daily()
    elif cmd == "daemon": threading.Thread(target=scheduler, daemon=True).start(); serve(port)
    elif cmd == "seed-demo": seed_demo()
    else: print(__doc__)
