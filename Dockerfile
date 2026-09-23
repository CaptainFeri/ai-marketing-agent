# Single image for the API and every worker; the command decides the role.
#
# Pinned to bookworm, not the floating "slim" tag: Playwright's own
# `--with-deps` (below) hard-codes a package list per Debian release, and
# lags behind whatever release "slim" currently resolves to (it broke
# against trixie — package names like libatk-bridge2.0-0t64 that trixie
# doesn't have). bookworm is a release Playwright has supported for a while.
FROM python:3.11-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Some Docker hosts advertise IPv6 routes for package mirrors that are
# actually black-holed, so apt hangs on IPv6 until it times out before ever
# trying IPv4. Force IPv4 for apt everywhere in this image (this also covers
# playwright install --with-deps below, which shells out to apt-get itself).
RUN echo 'Acquire::ForceIPv4 "true";' > /etc/apt/apt.conf.d/99force-ipv4

# ffmpeg is needed by the media_cpu worker for muxing (handoff section 8).
# espeak-ng is the default TTS backend (app/services/tts_backend.py) — real,
# offline narration with no model download, standing in for Piper/Chatterbox
# until phase 0 validates those. fonts-noto-core gives Chromium real glyphs
# for Persian and Arabic script — without it the overlay renderer
# (app/services/image_overlay.py) draws boxes instead of text, which defeats
# the entire reason it exists.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential libpq5 ffmpeg espeak-ng curl fonts-noto-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/app

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

# One Chromium, shared by every worker that renders an overlay. Installed to
# a fixed, world-readable path so the unprivileged user below can launch it;
# left unset in Settings.playwright_executable_path so Playwright resolves
# its own bundled build here rather than needing an explicit override (that
# setting exists for dev sandboxes with a browser at a nonstandard path).
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers

# The OS packages Chromium needs (Playwright's own dependency list) come
# from the Debian mirror already proven reachable above — always install
# these, on every host.
RUN python -m playwright install-deps chromium

# The Chromium *binary* itself is a different story: Playwright fetches it
# from a hard-coded cdn.playwright.dev URL with no mirror override (unlike
# every other asset in this Dockerfile), and that CDN 403s "not available
# in your location" from some hosts (handoff section 13's access-from-Iran
# risk, hit for real in phase 0). SKIP_CHROMIUM_DOWNLOAD=true skips this
# fetch; scripts/download-chromium.ps1 fetches the identical build (same
# pinned playwright version, see pyproject.toml) on an unrestricted machine
# instead, for PW_BROWSERS_DIR (docker-compose.yml) to mount at
# /opt/pw-browsers in its place. Default is false: everywhere the CDN isn't
# blocked, this Just Works with no extra step.
ARG SKIP_CHROMIUM_DOWNLOAD=false
RUN mkdir -p /opt/pw-browsers \
 && if [ "$SKIP_CHROMIUM_DOWNLOAD" != "true" ]; then python -m playwright install chromium; fi \
 && chmod -R a+rX /opt/pw-browsers

COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts

# Never run as root: a compromised connector should not own the container.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /srv/app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
