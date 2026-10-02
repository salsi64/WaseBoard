# Serveur WaseBoard — image autonome (Python + ffmpeg + libopus).
# Les données (catalogue, sons, sessions...) vivent dans le volume /data, jamais dans l'image.
FROM python:3.12-slim

# ffmpeg : décodage des sons ; libopus0 : le serveur envoie du PCM, discord.py l'encode donc en Opus.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libopus0 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py diagnostics.py wb_config.py ./

# Utilisateur sans privilèges ; /data lui appartient (un volume nommé hérite de ces droits à sa création).
RUN useradd --system --uid 10001 --home-dir /app waseboard \
    && mkdir /data && chown waseboard /data
USER waseboard

ENV WASEBOARD_DATA_DIR=/data \
    WASEBOARD_HTTP_HOST=0.0.0.0 \
    WASEBOARD_HTTP_PORT=5005 \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 5005

# /health répond 200 quand le bot est connecté à Discord (503 pendant le démarrage ou une reconnexion).
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:%s/health' % os.environ['WASEBOARD_HTTP_PORT'], timeout=4)"

CMD ["python", "server.py"]
