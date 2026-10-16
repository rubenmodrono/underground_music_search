# music-search

Busca joyas underground en Bandcamp (y Hypeddit, básico), las pasa por un auditor que aprende tus gustos y te las muestra con preview.

## Despliegue local
Requiere Python 3.9+ (sin dependencias).

    python3 app.py daemon        # web en http://localhost:8080 + búsqueda diaria a las 10:00 (hora local)
    python3 app.py seed-demo     # opcional: datos falsos para ver la interfaz

Solo web: `python3 app.py serve --port 8080`. Con cron: `0 10 * * * cd /ruta/music-search && python3 app.py run-daily`.
CLI suelta: `python3 musicsearch.py bandcamp -t "raw techno" --pages 2`.

Estado: la web y el aprendizaje están probados con datos demo; las búsquedas reales en Bandcamp/Hypeddit no se han probado todavía.
