# Multi-stage build: React SPA -> FastAPI image that also serves the built SPA.
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# Tesseract enables OCR for scanned PDFs (omit this line to run without OCR; scans are then routed to manual review)
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/ ./
COPY --from=web /web/dist /app/frontend/dist
RUN useradd -m app && mkdir -p /data && chown -R app /data /app
ENV ENV=production STORAGE_ROOT=/data/storage
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fs http://localhost:${PORT:-8000}/api/health || exit 1
# Hosts mount the persistent disk owned by root: fix its ownership first, then run the app as the unprivileged user.
# The port comes from $PORT (Render, Railway, ...) and defaults to 8000; --forwarded-allow-ips lets the app see https behind the host's proxy.
CMD ["sh", "-c", "chown -R app /data; exec runuser -u app -- sh -c 'alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=*'"]
