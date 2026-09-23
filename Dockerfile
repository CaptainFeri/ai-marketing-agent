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
COPY scripts ./scripts
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

# The Chromium *binary* itself is fetched by scripts/install_chromium.py,
# not `playwright install chromium` — that hard-codes a cdn.playwright.dev
# URL with no override (unlike every other asset in this Dockerfile), and
# both it and storage.googleapis.com (the *other* official home of the same
# build) were confirmed 403ing "not available in your location" from real
# hardware in phase 0 (handoff section 13's access-from-Iran risk). The
# script's own docstring has the full story and the fallback if its mirror
# ever needs to change too.
RUN python scripts/install_chromium.py \
 && chmod -R a+rX /opt/pw-browsers

COPY alembic.ini ./
COPY migrations ./migrations

# Never run as root: a compromised connector should not own the container.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /srv/app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
