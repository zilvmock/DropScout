# syntax=docker/dockerfile:1

FROM python:3.12-slim AS runtime

# Environment tweaks for predictable, quiet Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONOPTIMIZE=2 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install Python dependencies first for better layer caching
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the source
COPY . .

# Create an unprivileged user and writable data dir
RUN useradd -m -u 10001 -s /usr/sbin/nologin appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

USER appuser

# Persist bot state/config outside the container unless overridden
VOLUME ["/app/data"]

# No ports exposed; bot uses outbound connections only

# Entrypoint: start the Discord bot
CMD ["python", "-OO", "DropScout.py"]
