#!/usr/bin/env python3
"""Fetches Chromium and chromium-headless-shell for Playwright without
going through cdn.playwright.dev.

That CDN (and, confirmed in phase 0, storage.googleapis.com — the same
Chrome for Testing build's *other* official home) has been seen 403ing
"not available in your location" (handoff section 13's access-from-Iran
risk). Playwright hard-codes cdn.playwright.dev for this one download with
no override, unlike everything else it fetches.

npmmirror.com (Alibaba Cloud, a different provider entirely, confirmed
reachable where the other two were not) mirrors the same official Chrome
for Testing builds Google publishes, at a predictable URL. This script
reads the exact revision and version playwright's own installed
browsers.json already pins - so it can never drift from what the installed
playwright package expects - and downloads from there instead, laying the
result out exactly the way Playwright's own installer would (including the
INSTALLATION_COMPLETE marker file it checks for), so Playwright picks it up
with no further configuration on its end.

Run inside the Docker build, after `pip install .` (needs playwright's own
browsers.json already installed) with PLAYWRIGHT_BROWSERS_PATH set. Safe to
re-run - skips anything already marked complete.

If npmmirror.com ever stops working too, the fix is the same shape: find
another mirror of the same Chrome for Testing builds
(https://googlechromelabs.github.io/chrome-for-testing/) and change
MIRROR_BASE below.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

MIRROR_BASE = "https://cdn.npmmirror.com/binaries/chrome-for-testing"

# Playwright's own name -> the platform-specific filename this mirror (and
# Google's own bucket) publishes it under.
TARGETS = {
    "chromium": "chrome-linux64.zip",
    "chromium-headless-shell": "chrome-headless-shell-linux64.zip",
}


def browsers_json_path() -> Path:
    import playwright

    return Path(playwright.__file__).parent / "driver" / "package" / "browsers.json"


def install_one(name: str, filename: str, browser: dict, dest_root: Path) -> None:
    revision = browser["revision"]
    version = browser["browserVersion"]
    # Matches Playwright's own registry/index.ts: dashes become underscores
    # in the directory prefix, then "-<revision>".
    target_dir = dest_root / f"{name.replace('-', '_')}-{revision}"
    marker = target_dir / "INSTALLATION_COMPLETE"
    if marker.exists():
        print(f"{name}: already installed at {target_dir}")
        return

    url = f"{MIRROR_BASE}/{version}/linux64/{filename}"
    print(f"{name} {version} (revision {revision}): downloading from {url}")
    zip_path = dest_root / filename
    urllib.request.urlretrieve(url, zip_path)  # noqa: S310 - fixed https mirror

    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(target_dir)
    zip_path.unlink()
    marker.write_text("")
    print(f"{name}: installed at {target_dir}")


def main() -> int:
    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not browsers_path:
        print("PLAYWRIGHT_BROWSERS_PATH must be set", file=sys.stderr)
        return 1
    dest_root = Path(browsers_path)
    dest_root.mkdir(parents=True, exist_ok=True)

    catalogue = json.loads(browsers_json_path().read_text())
    by_name = {b["name"]: b for b in catalogue["browsers"]}

    for name, filename in TARGETS.items():
        install_one(name, filename, by_name[name], dest_root)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
