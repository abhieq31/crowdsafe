# Room to Breathe control-room server: API, dashboard, alerts, event record.
#   docker run -p 8000:8000 -v $PWD/data:/data -e RTB_TOKEN=change-me ghcr.io/abhipatelz/roomtobreathe
# With /data/venue.json present it runs live; without it, the demo.
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[server]" \
 && useradd --uid 10001 --create-home roomtobreathe \
 && mkdir -p /data/logs && chown -R roomtobreathe /data
COPY docker/entrypoint-server.sh /usr/local/bin/roomtobreathe-server
ENV RTB_LOG_DIR=/data/logs PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
# Starts as root only to make the mounted /data/logs writable, then drops to the roomtobreathe user.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
ENTRYPOINT ["roomtobreathe-server"]
