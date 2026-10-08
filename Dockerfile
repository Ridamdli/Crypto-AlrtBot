# Crypto-AlrtBot — live signal bot (15m loop, Telegram delivery)
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# Secrets are NEVER baked into the image.
# Provide them at runtime: --env-file .env  OR  -e TELEGRAM_BOT_TOKEN=... -e TELEGRAM_CHAT_ID=...
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "src.service"]
