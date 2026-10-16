"""Diagnostico: abre Bandcamp con el navegador de Playwright y muestra que pasa.
   python test_browser.py"""
import traceback, musicsearch as ms
try:
    h = ms.browser_get("https://bandcamp.com/tag/techno?sort_field=date")
    print("OK, bytes:", len(h), "| data-blob:", "data-blob" in h, "| challenge:", "Client Challenge" in h)
except Exception:
    traceback.print_exc()
