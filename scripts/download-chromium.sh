#!/usr/bin/env bash
# Downloads the Chromium build the Dockerfile's overlay renderer needs
# (app/services/image_overlay.py), so it can be copied onto a GPU host
# where cdn.playwright.dev is blocked. Same job as download-chromium.ps1,
# for anyone who'd rather not install PowerShell for this one thing.
#
# Run on any Linux/Mac machine with unrestricted internet — your own box,
# a spare VM, or a free GitHub Codespace opened on this repo (Settings ->
# Code -> Codespaces -> New codespace; it's a full Linux dev box in the
# browser with normal outbound internet, no local install of anything).
#
#   ./scripts/download-chromium.sh [dest-dir]   # default ./pw-browsers
#
# Re-running is safe; Playwright's own installer skips what's already
# present and valid under $dest.

set -euo pipefail

# Keep this in sync with pyproject.toml's playwright== pin by hand.
PLAYWRIGHT_VERSION="1.63.0"
DEST="${1:-./pw-browsers}"

command -v python3 >/dev/null || { echo "python3 is required." >&2; exit 1; }

echo "Installing playwright==${PLAYWRIGHT_VERSION} (matching pyproject.toml)..."
python3 -m pip install --quiet --upgrade "playwright==${PLAYWRIGHT_VERSION}"

mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"

echo
echo "==> Chromium (playwright ${PLAYWRIGHT_VERSION}) -> ${DEST}"
PLAYWRIGHT_BROWSERS_PATH="$DEST" python3 -m playwright install chromium

chmod -R a+rX "$DEST"

echo
echo "Done. Copy '${DEST}' onto the GPU host, set PW_BROWSERS_DIR to that"
echo "path in .env, set SKIP_CHROMIUM_DOWNLOAD=true, and copy"
echo "docker-compose.override.yml.example to docker-compose.override.yml"
echo "(docs/deployment.md has the full sequence)."
