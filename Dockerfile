FROM node:22.18-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=https://pypi.org/simple \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PATH=/app/backend/.venv/bin:$PATH \
    LLM_MODE=mock \
    APP_ENV=development \
    DATA_DIR=/app/data \
    DATABASE_PATH=/app/data/app.db
WORKDIR /app/backend
RUN python -m pip install --no-cache-dir uv==0.13.6
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev \
    && uv run --frozen --no-dev playwright install --with-deps chromium
COPY backend/app ./app
COPY backend/migrations ./migrations
COPY contracts /app/contracts
COPY templates /app/templates
COPY assets /app/assets
COPY --from=frontend /build/frontend/dist /app/frontend/dist
RUN useradd --create-home --uid 10001 demo \
    && mkdir -p /app/data \
    && chown -R demo:demo /app/data
USER demo
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
