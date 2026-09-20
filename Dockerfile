# Single-service Railway deployment: FastAPI owns the public port and serves
# the exported Next.js dashboard plus /api and /ws/ui.
FROM node:20-alpine AS ui-build
WORKDIR /src/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ENV=production

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend ./backend
COPY --from=ui-build /src/frontend/out ./frontend/out
COPY tests ./tests
COPY scripts ./scripts
COPY README.md PROJECT.md ORIGINAL_REQUEST.md ./

EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
