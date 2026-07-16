# syntax=docker/dockerfile:1
# Minimal image for Oracle Cloud Container Instances / OCI Functions.
FROM python:3.11-slim AS base

# Keep Python lean and predictable in containers.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first so the layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source.
COPY . .

# Run as an unprivileged user for security.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Adjust to your ASGI server / entrypoint once adapters are wired up.
CMD ["python", "-m", "main"]
