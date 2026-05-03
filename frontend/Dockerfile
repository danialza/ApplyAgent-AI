# --- 1. deps ----------------------------------------------------------
FROM node:20-alpine AS deps
WORKDIR /app

# Install only the lockfile + manifest so the layer caches across rebuilds.
COPY package.json ./
# package-lock.json is committed in production; copy it if present.
COPY package-lock.json* ./
RUN npm install --no-audit --no-fund

# --- 2. build ---------------------------------------------------------
FROM node:20-alpine AS build
WORKDIR /app

# `NEXT_PUBLIC_*` is baked into the client bundle at build time, so it
# must be available here. Pass via `--build-arg NEXT_PUBLIC_API_URL=...`.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}

COPY --from=deps /app/node_modules ./node_modules
COPY . .

RUN npm run build

# --- 3. runtime -------------------------------------------------------
FROM node:20-alpine AS runtime
WORKDIR /app

ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Run as a non-root user.
RUN addgroup -g 1001 -S nodejs && adduser -S -u 1001 -G nodejs nextjs

# Standalone build = a self-contained server bundle (no node_modules copy).
COPY --from=build --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=build --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=build --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD wget -qO- http://127.0.0.1:3000 >/dev/null || exit 1

CMD ["node", "server.js"]
