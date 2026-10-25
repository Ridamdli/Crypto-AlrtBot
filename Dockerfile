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
FROM python:3.12-slim

# pandas/psycopg ship manylinux wheels for 3.12, so no gcc/libpq-dev needed.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Deps first so source edits do not invalidate the pip layer.
COPY requirements.txt .
RUN pip install -r requirements.txt

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

CMD ["sh", "-c", "if [ \"${SERVICE_TYPE:-main}\" = \"mirror\" ]; then exec python -m mirror; else exec python -m src.service; fi"]
