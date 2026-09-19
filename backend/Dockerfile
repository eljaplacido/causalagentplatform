# Build from the REPOSITORY ROOT, because the backend depends on packages/causal-core:
#
#     docker build -f backend/Dockerfile -t causal-platform-backend .
#
# (The previous version built from backend/ and copied only pyproject.toml before
# `pip install .`, which cannot build a package whose sources are absent.)

# ── Build stage ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# The causal kernel first (a local package, not on PyPI yet), then the backend.
COPY packages/causal-core ./causal-core
COPY backend/pyproject.toml ./backend/pyproject.toml
COPY backend/src ./backend/src
COPY backend/mcp_server ./backend/mcp_server
RUN pip install --no-cache-dir --prefix=/install ./causal-core "./backend[production]"

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
COPY backend/src ./src/

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
