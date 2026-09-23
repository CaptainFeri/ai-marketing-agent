#requires -Version 5.1
<#
.SYNOPSIS
    Downloads the Chromium build the Dockerfile's overlay renderer needs
    (app/services/image_overlay.py), so it can be copied onto a GPU host
    where cdn.playwright.dev is blocked.

.DESCRIPTION
    Run this on any machine with unrestricted internet access. It installs
    the exact playwright version pinned in pyproject.toml (the two must
    never drift apart - a mismatched Chromium revision refuses to launch)
    and asks it to download only chromium into a local folder (-Dest,
    default .\pw-browsers) - it never touches this app itself.

    cdn.playwright.dev has been seen 403ing "not available in your
    location" for Chromium's binary specifically (handoff section 13's
    access-from-Iran risk, hit for real in phase 0) even though every
    other download in this project's Dockerfile has a mirror override;
    Playwright hard-codes that one URL with no override, so the only real
    fix is downloading it somewhere the block doesn't apply.

    Re-running this script is safe; Playwright's own installer skips
    what's already present and valid under -Dest.

.PARAMETER Dest
    Destination folder. Gets the same internal layout Playwright's own
    PLAYWRIGHT_BROWSERS_PATH expects (versioned chromium-<rev>/ subfolders),
    so it can be pointed at directly.

.EXAMPLE
    .\download-chromium.ps1

.EXAMPLE
    .\download-chromium.ps1 -Dest D:\pw-browsers
#>

[CmdletBinding()]
param(
    [string]$Dest = ".\pw-browsers"
)

$ErrorActionPreference = "Stop"

# Keep this in sync with pyproject.toml's playwright== pin by hand - there's
# no single source of truth to read it from without also requiring this
# machine to have the rest of the repo's Python toolchain installed.
$PlaywrightVersion = "1.63.0"

function Assert-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name is required but was not found on PATH. Install Python 3.9+ first: https://www.python.org/downloads/"
    }
}

Assert-Command python

Write-Host "Installing playwright==$PlaywrightVersion (matching pyproject.toml) via pip..." -ForegroundColor Cyan
python -m pip install --upgrade "playwright==$PlaywrightVersion" | Out-Null

$destPath = New-Item -ItemType Directory -Force -Path $Dest
$resolvedDest = (Resolve-Path $destPath).Path

Write-Host ""
Write-Host "==> Chromium (playwright $PlaywrightVersion) -> $resolvedDest" -ForegroundColor Green

$env:PLAYWRIGHT_BROWSERS_PATH = $resolvedDest
python -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
    throw "playwright install chromium failed (exit $LASTEXITCODE) - see message above."
}

Write-Host ""
Write-Host "Done. Copy '$resolvedDest' onto the GPU host, set PW_BROWSERS_DIR to that path in .env, set SKIP_CHROMIUM_DOWNLOAD=true, and copy docker-compose.override.yml.example to docker-compose.override.yml (docs/deployment.md has the full sequence)." -ForegroundColor Cyan
Write-Host "On Linux/Mac, make sure it's world-readable before copying it over: chmod -R a+rX '$resolvedDest'" -ForegroundColor Yellow
