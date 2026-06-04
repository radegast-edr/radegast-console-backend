# Stage 1: Build Svelte Frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /web
# Copy frontend dependency files first to leverage Docker cache
COPY web/package.json web/package-lock.json web/.npmrc ./
RUN npm install -g npm@latest && npm ci

# Copy the rest of the web folder and build
COPY web/ ./
ENV PUBLIC_BACKEND_URL=/api/v1
RUN npm run build

# Stage 2: Backend using official python image and copying uv
FROM python:3.11-slim AS backend
WORKDIR /app

# Install uv using the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy python project configuration files
COPY pyproject.toml uv.lock ./

# Install dependencies (without installing the project itself yet)
RUN uv sync --frozen --no-dev --no-install-project

# Copy built frontend from Stage 1 into the correct place
COPY --from=frontend-builder /web/build ./web/build

# Copy the rest of the backend files
COPY app ./app
COPY agent/config ./agent/config
COPY hatch_build.py ./

# Install the project itself
RUN uv sync --frozen --no-dev

# Expose backend port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

# Run backend using uvicorn directly from virtual environment
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*", "--workers", "4"]
