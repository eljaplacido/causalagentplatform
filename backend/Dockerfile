# Build from the REPOSITORY ROOT, because the backend depends on packages/causal-core:
#
#     docker build -f backend/Dockerfile -t causal-platform-backend .
#
# (The previous version built from backend/ and copied only pyproject.toml before
# `pip install .`, which cannot build a package whose sources are absent.)

# ── Build stage ────────────────────────────────────────────────────────────
FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS builder

WORKDIR /build

# Install build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Into a virtual environment the production stage copies whole. Every distribution
# from an index comes from requirements/image.txt, pinned and checked against its
# sha256 (--require-hashes), resolving nothing else (--no-deps); pip itself is in the
# lock. Then this checkout's own packages -- the causal kernel, the decision layer and
# the backend, which are not on an index and so not in the lock -- built with the
# hatchling the lock installed (--no-build-isolation: no unhashed build backend is
# fetched) and pulling in nothing (--no-deps). `pip check` proves the result whole.
# After a dependency change, re-lock: python3 scripts/py_lock.py relock
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements/image.txt ./requirements/image.txt
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements/image.txt
COPY packages/causal-core ./causal-core
COPY packages/causal-decision ./causal-decision
COPY backend/pyproject.toml ./backend/pyproject.toml
COPY backend/src ./backend/src
RUN pip install --no-cache-dir --no-deps --no-build-isolation \
        ./causal-core ./causal-decision "./backend[production]" \
    && pip check \
    && python -m pip uninstall --yes --quiet pip

# ── Production stage (distroless-style slim) ───────────────────────────────
FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS production

WORKDIR /app

# Non-root user (security requirement from CYBERSECURITY.md)
RUN groupadd -r causal && useradd -r -g causal causal

# Install runtime-only system deps, and Debian's security updates: the pinned base
# image lags the archive (2026-10-01: openssl CVE-2026-75804/-84782 and pcre2
# CVE-2026-103111 were fixed in deb13u3 while python:3.12-slim still shipped deb13u2,
# and the image scan fails a HIGH finding that has a fix). The digest still pins
# everything else; a base-image bump makes this a no-op until the next lag.
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# The locked environment from the builder (same base image, so the same interpreter
# path). Nothing installs packages at run time, so neither it nor the base image
# keeps pip (the builder removed the venv's, after `pip check`; the base image's goes
# here), and a scan of this image finds no installer or its vendored copies.
COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
RUN rm -rf /usr/local/lib/python3.12/site-packages/pip /usr/local/lib/python3.12/site-packages/pip-*.dist-info \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12

# Copy application source, and the versioned prompt templates the agents render
# (src/agents/prompt_templates.py finds them at /app/prompts; PROMPTS_DIR overrides)
COPY backend/src ./src/
COPY prompts ./prompts/

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
