# ============================================================
# Production Dockerfile — multi-stage
#   builder : tạo virtualenv và cài dependencies
#   runtime : python:3.11-slim sạch, chỉ copy venv + code, chạy bằng user non-root
# Build:  docker build -t day14-agent .
# Run:    docker run -p 8000:8000 -e AGENT_API_KEY=... -e REDIS_URL=... day14-agent
# ============================================================

# ── Stage 1: builder ──────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /build
# requirements trước, code sau → sửa code không phải cài lại dependencies (layer cache)
COPY requirements.txt .
RUN pip install -r requirements.txt


# ── Stage 2: runtime ──────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

RUN groupadd --system app && useradd --system --gid app --home-dir /app --no-create-home app

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app app/ ./app/
COPY --chown=app:app utils/ ./utils/

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/health', timeout=4)"]

# `exec` để uvicorn thay thế sh và là PID 1 → nhận SIGTERM trực tiếp khi `docker stop`.
# PORT do platform (Railway/Render) cấp; graceful shutdown tối đa 8s (< 10s của docker stop).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --timeout-graceful-shutdown 8 --no-server-header"]
