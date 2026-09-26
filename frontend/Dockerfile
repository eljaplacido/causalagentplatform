# Build from the REPOSITORY ROOT: the frontend is an npm workspace whose lock
# (package-lock.json) and whose @causalplatform/semantic-types dependency live
# outside frontend/:
#
#     docker build -f frontend/Dockerfile -t causal-platform-frontend .
#
# frontend/Dockerfile.dockerignore replaces the root .dockerignore for this build
# (that one leaves frontend/ out, for the backend image). The previous version of
# this file built from frontend/ -- where there is no lock, so `npm ci` failed --
# and copied a standalone output next.config.ts did not ask for.

# ── Build stage ────────────────────────────────────────────────────────────
FROM node:26-alpine@sha256:0b36e8c136b94cd4fcf02188228e76c31ad5872eef3fec8cbd2eee500cfd9e80 AS builder
WORKDIR /repo

# The workspace manifests first, so the install layer is cached across source edits.
# npm ci installs exactly package-lock.json, integrity hashes checked.
COPY package.json package-lock.json ./
COPY packages/semantic-types/package.json ./packages/semantic-types/package.json
COPY frontend/package.json ./frontend/package.json
RUN npm ci --no-audit --no-fund

COPY packages/semantic-types ./packages/semantic-types
COPY frontend ./frontend

ENV NEXT_TELEMETRY_DISABLED=1
# The types package's dist/ is what the frontend imports; npm ci does not build it.
RUN npm run build --workspace=packages/semantic-types \
    && npm run build --workspace=frontend

# ── Production stage ───────────────────────────────────────────────────────
FROM node:26-alpine@sha256:0b36e8c136b94cd4fcf02188228e76c31ad5872eef3fec8cbd2eee500cfd9e80 AS production
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# Non-root user
RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 -G nodejs nextjs

# The standalone server (next.config.ts: output "standalone", traced from the
# repository root, so the app sits under frontend/) and its static assets. npm and
# its bundled dependencies are not needed to run it, and are removed.
COPY --from=builder --chown=nextjs:nodejs /repo/frontend/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /repo/frontend/.next/static ./frontend/.next/static
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx \
           /usr/local/lib/node_modules/corepack /usr/local/bin/corepack

USER nextjs

EXPOSE 3000

CMD ["node", "frontend/server.js"]
