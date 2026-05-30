# Stage 1: Build Svelte Frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /web
# Copy frontend dependency files first to leverage Docker cache
COPY web/package.json web/package-lock.json ./
RUN npm ci

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

# Install the project itself
RUN uv sync --frozen --no-dev

# Expose backend port
EXPOSE 8000

# Run backend using uvicorn through uv
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--workers", "4"]
