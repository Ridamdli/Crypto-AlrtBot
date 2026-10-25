# Crypto-AlrtBot — single image for both deploy units (main bot + mirror bot).
#
# SERVICE_TYPE selects the entrypoint:
#   main   (default) -> python -m src.service   dashboard on $PORT + 15m scheduler loop
#   mirror           -> python -m mirror         MIRROR_MODE=watch|auto, no HTTP listener
#
# No secrets are baked in. Everything comes from the platform env:
#   DATABASE_URL (required in production), TELEGRAM_*, ADMIN_TOKEN, INVO_*, MIMIC_*, ...
#
# Dashboard port: $PORT, default 8080. Platform health probe -> GET /healthz (200 {"ok":true}).

# --- builder: resolves and installs the pinned deps into an isolated venv -------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Every dependency resolves to a cp312 manylinux wheel, so there is no compiler
# and no libpq-dev here. The stage boundary exists so that a future native dep
# (one that needs gcc) cannot leak its build toolchain into the runtime image.
RUN python -m venv /opt/venv

COPY requirements.txt .
RUN /opt/venv/bin/pip install -r requirements.txt

# --- runtime: venv + code only ------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

COPY src/ ./src/
COPY mirror/ ./mirror/
# Alembic ships in the image so a fresh DB can be initialised in-container:
#   docker run --rm -e DATABASE_URL=... crypto-alrtbot alembic upgrade head
COPY alembic/ ./alembic/
COPY alembic.ini .

# Non-root runtime. Some code paths are still file-backed and write under the
# working directory, so the data dirs must exist and be writable by the app user.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/data_store /app/mirror_data \
    && chown -R app:app /app
USER app
ENV HOME=/home/app

EXPOSE 8080

# SERVICE_TYPE-aware on purpose: the mirror unit opens no HTTP listener, so
# probing a port there would restart-loop it forever. curl is not in
# python:3.12-slim, hence urllib. urllib raises on any non-2xx, which is the
# non-zero exit HEALTHCHECK needs. $PORT is honoured, same as the dashboard.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import os, sys, urllib.request; \
sys.exit(0) if os.getenv('SERVICE_TYPE', 'main') == 'mirror' else \
urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.getenv('PORT', '8080'), timeout=8).read()"

CMD ["sh", "-c", "if [ \"${SERVICE_TYPE:-main}\" = \"mirror\" ]; then exec python -m mirror; else exec python -m src.service; fi"]
