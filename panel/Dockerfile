# Pure client of the FastAPI service (docs/panel.md) — this image only
# serves the built Next.js app, no server-side database or queue access.
FROM node:22-slim AS deps
WORKDIR /srv/panel
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-slim AS builder
WORKDIR /srv/panel
COPY --from=deps /srv/panel/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* variables are inlined into the client bundle at build time,
# not read at container start — this must be the URL the *browser* can
# reach the api service at (the host's public address, not the Docker
# network's internal one), so it has to be a build arg, not a plain env var
# on the panel service in docker-compose.yml.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
RUN npm run build

FROM node:22-slim AS runner
WORKDIR /srv/panel
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0
# output: "standalone" (next.config.ts) traces only the files server.js
# actually needs, instead of shipping the full node_modules tree.
COPY --from=builder /srv/panel/.next/standalone ./
COPY --from=builder /srv/panel/.next/static ./.next/static
COPY --from=builder /srv/panel/public ./public

RUN useradd --create-home --uid 10001 appuser && chown -R appuser /srv/panel
USER appuser

EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "fetch('http://localhost:3000/login').then(r=>{if(!r.ok)throw new Error(r.status)}).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
