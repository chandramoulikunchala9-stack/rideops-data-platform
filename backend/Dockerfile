# RideOps API image. Build from the repository root (it needs ../pipelines too):
#   docker build -f backend/Dockerfile -t rideops-api .
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    RIDEOPS_SQL_DIR=/app/pipelines/sql \
    RIDEOPS_INCIDENTS_DIR=/app/monitoring/incidents

WORKDIR /app
COPY pipelines/pyproject.toml pipelines/README.md pipelines/
COPY pipelines/src pipelines/src
COPY pipelines/sql pipelines/sql
COPY monitoring/incidents monitoring/incidents
COPY backend/pyproject.toml backend/README.md backend/
COPY backend/src backend/src
RUN pip install --no-cache-dir ./pipelines "./backend[engines]" \
    && useradd --create-home --uid 10001 rideops
USER rideops

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/v1/health', timeout=4)"

# Hosting platforms (Render, Fly, Cloud Run, ...) inject $PORT; --proxy-headers trusts their TLS proxy.
CMD ["sh", "-c", "uvicorn rideops_api.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
