# Stage 1: Build Python Package (which auto-builds frontend)
FROM python:3.11-slim AS builder
WORKDIR /src

# Install Node.js and npm (needed by hatch_build.py to build frontend)
RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install uv using the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy Svelte dependencies first to leverage Docker cache
COPY web/package.json web/package-lock.json web/.npmrc ./web/
RUN cd web && npm ci

# Copy the rest of the project files
COPY . .

# Build the python package (which compiles frontend and packages everything into a wheel)
ENV PUBLIC_BACKEND_URL=/
RUN uv build --wheel

# Stage 2: Production Stage
FROM python:3.11-slim AS production
WORKDIR /app

# Install uv using the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Create a clean virtual environment
RUN uv venv /app/.venv

# Copy the built wheel from Stage 1 builder
COPY --from=builder /src/dist/*.whl ./

# Copy migrations, alembic.ini, apply-migrations.py, entrypoint.sh
COPY --from=builder /src/migrations ./migrations
COPY --from=builder /src/alembic.ini /src/apply-migrations.py /src/entrypoint.sh ./
RUN chmod +x entrypoint.sh apply-migrations.py

# Install the wheel package inside the virtual environment
RUN uv pip install --python /app/.venv *.whl

# Expose backend port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]

# Run backend using the installed package's CLI script from the virtual environment
CMD ["--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--apply-migrations"]
