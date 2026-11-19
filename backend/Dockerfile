# RideOps API image (FastAPI + uvicorn). Build from the repository root:
#   docker build -f backend/Dockerfile -t rideops-api .
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY sql ./sql
COPY monitoring ./monitoring
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 rideops
USER rideops

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/v1/health', timeout=4)"

# Hosting platforms (Render, Fly, Cloud Run, ...) inject $PORT; --proxy-headers trusts their TLS proxy.
CMD ["sh", "-c", "uvicorn rideops.api.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
