# ── Build stage ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps into isolated location
COPY pyproject.toml ./
RUN pip install --no-cache-dir --prefix=/install ".[production]"

# ── Production stage (distroless-style slim) ───────────────────────────────
FROM python:3.12-slim AS production

WORKDIR /app

# Non-root user (security requirement from CYBERSECURITY.md)
RUN groupadd -r causal && useradd -r -g causal causal

# Install runtime-only system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY src/ ./src/

# Read-only root filesystem where possible (CYBERSECURITY.md §8.3)
RUN chown -R causal:causal /app

USER causal

# Cloud Run listens on $PORT (default 8080)
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080

# Uvicorn with multiple workers for production
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "4", "--proxy-headers", "--forwarded-allow-ips", "*"]
