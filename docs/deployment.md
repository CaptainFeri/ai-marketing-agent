# Deployment

Single server, Docker Compose (handoff section 4). Kubernetes is phase 4.

## Prerequisites

- Ubuntu with the NVIDIA driver, CUDA and the NVIDIA container toolkit
- Docker and Docker Compose
- **64 GB RAM minimum**, 128 GB preferred — model weights are offloaded from
  VRAM to RAM during window switches (handoff section 5)
- NVMe storage; load time is a direct cost every time the window switches

## First run

```bash
cp .env.example .env
```

`.env.example` is the canonical, fully-documented reference for every
setting. Four run modes each have their own file on top of it —
`.env.example.staging`, `.env.example.production`, `.env.example.gpu-host`
— each a short delta showing only what changes for that mode (real secrets,
real Postgres/Redis/S3, which backends are real vs. simulated); copy the
values you need from the matching one into your real `.env` rather than
`cp`-ing it directly, since they carry placeholder secrets. See
`docs/phase-0-checklist.md` for what "GPU host" mode still needs before its
GPU-backed settings can move past `simulated`.

Fill in, at minimum:

```bash
# 48 random bytes
python -c "import secrets; print(secrets.token_urlsafe(48))"
# Fernet key for channel credentials
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

The application refuses to start with `ENVIRONMENT=staging|production` if
`SECRET_KEY` is still the placeholder, `CREDENTIAL_ENCRYPTION_KEY` is empty, or
`DEBUG` is on. That check is in `app/core/config.py`.

```bash
docker compose up -d postgres redis minio
docker compose run --rm api alembic upgrade head
docker compose up -d
```

Uncomment the `deploy.resources.reservations.devices` block on `gpu-worker`
once the NVIDIA container toolkit is installed.

## Database roles

Two roles, and the separation is load-bearing:

| Role | `BYPASSRLS` | Used by |
|---|---|---|
| `app_system` | yes | Alembic, the GPU scheduler, the quota allocator, login |
| `app` | **no** | every API request |

`scripts/init_roles.sql` creates `app` and grants it the default privileges on
tables created later by migrations. It runs once, when the postgres volume is
first created.

If the application role held `BYPASSRLS`, the policies in migration 0002 would
be decorative. Verify after any role change:

```sql
SELECT rolname, rolbypassrls FROM pg_roles WHERE rolname IN ('app', 'app_system');
```

## pgvector

Migration 0003 creates the embedding store and needs the `vector` extension —
the `pgvector/pgvector:pg16` image in `docker-compose.yml` ships with it. On a
server without it the migration stops with an explanation rather than silently
creating a different schema. `ALLOW_MISSING_PGVECTOR=1` skips it knowingly,
which disables retrieval-augmented research but leaves everything else working.

## Backups (phase 1, week 9)

Automated: `maintenance.run_daily_backup` (`app/services/backup.py`), on the
Celery beat schedule at 02:30 UTC, before both quota allocation and the
metrics sweep. Daily, and both halves matter together — a Postgres dump
without the matching MinIO objects restores rows pointing at files that no
longer exist:

```
BACKUP_DIR/
  2026-01-15/
    postgres.sql.gz   # gzip'd `pg_dump` of the system database
    storage/           # every object in MinIO, written at a path mirroring
                        # its own key (tenant-prefixed, same as in MinIO)
  2026-01-14/
  ...
```

Anything older than `BACKUP_RETENTION_DAYS` (default 14) is pruned on every
run. `BACKUP_DIR` (default `./backups`) should point at a volume that
actually leaves the box — a bind mount to networked storage, or whatever the
operator already uses for host-level backups; this platform does not ship
an off-box copy step itself.

To back up or restore by hand instead:

```bash
docker compose exec -T postgres pg_dump -U app_system ai_marketing | gzip > pg-$(date +%F).sql.gz
mc mirror --overwrite local/ai-marketing /backups/minio/
```

Keep `CREDENTIAL_ENCRYPTION_KEY` with the backups **and** somewhere else. A
database restored without it cannot decrypt a single channel token, and every
customer has to reconnect every channel.

## Operating the GPU

- `gpu-worker` runs `scripts/check_models.py` before it starts (its
  `command` in `docker-compose.yml`) — a pre-flight report of what is
  present under `MODELS_DIR`, never a download. It only *blocks* startup
  over a backend that loads weights directly from this container's own
  disk and is configured to a non-default value (today, just
  `TTS_BACKEND=production`); a missing `MODELS_DIR` is otherwise fine as
  long as every backend is still at its simulated/espeak default.
- `gpu-worker` runs at `--concurrency 1`. Do not raise it. Two processes on one
  24 GB card produce an out-of-memory error, not throughput.
- `GET /api/v1/gpu/window` reports the loaded model family, the switch count
  and the queue depth per window.
- Frequent switches mean the two windows are interleaving badly. Raise
  `GPU_WINDOW_STARVATION_SECONDS` to batch more aggressively, at the cost of
  longer waits for the idle side.
- Heavy video runs in the night window. Move it with
  `GPU_NIGHT_WINDOW_START_HOUR` / `..._END_HOUR` to match the pilot customers'
  actual quiet hours.

## If cdn.playwright.dev is blocked

Seen for real in phase 0: the Dockerfile's `playwright install chromium`
step (needed for `app/services/image_overlay.py`'s Persian/Arabic text
rendering) can 403 with "not available in your location" — Playwright
hard-codes that one URL for Chromium specifically, with no mirror override
(unlike every other download in this project, which does have one).

1. On a machine with unrestricted internet, run
   `scripts/download-chromium.ps1` (pwsh works on Linux/Mac too, not just
   Windows). It installs the exact `playwright==` version pinned in
   `pyproject.toml` and downloads only Chromium into `.\pw-browsers`.
2. Copy that folder onto the GPU host; make sure it's world-readable
   (`chmod -R a+rX`) since the container runs as an unprivileged user.
3. In `.env`: set `SKIP_CHROMIUM_DOWNLOAD=true` and
   `PW_BROWSERS_DIR=/path/to/the/copied/folder`.
4. `cp docker-compose.override.yml.example docker-compose.override.yml` —
   this is what actually mounts the copied folder over `/opt/pw-browsers`
   for `gpu-worker` (the service that calls the overlay renderer). It's a
   separate opt-in file rather than a line in `docker-compose.yml` itself,
   because an unconditional mount there would shadow the image's own
   baked-in browser for every host the block doesn't apply to.
5. `docker compose build` (picks up the build arg) then `docker compose up -d`.

## Upgrades

```bash
docker compose build
docker compose run --rm api alembic upgrade head
docker compose up -d
```

Migrations run before the new code starts. Every migration here is reversible
except the data it would drop, so review `downgrade()` before relying on it.
