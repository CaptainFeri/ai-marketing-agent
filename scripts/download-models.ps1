#requires -Version 5.1
<#
.SYNOPSIS
    Downloads the phase-0 model weights the platform needs (docs/HANDOFF.md
    section on required models), so they can be copied onto the GPU host
    that will actually run them.

.DESCRIPTION
    Run this on any machine with internet access to huggingface.co. It never
    touches the app itself — it only populates a local folder tree
    (-ModelsRoot, default .\models) that mirrors what each backend expects:
    LLM, embedding, image, video, transcription, TTS and lip-sync weights.

    Uses the official `huggingface-cli` (Python, installed on demand via
    pip if missing) so downloads are resumable and content-addressed —
    re-running this script only fetches what's missing or changed.

    Some repos are gated on huggingface.co (you must click "Agree" on the
    model page once while logged in) and/or need a token:
    https://huggingface.co/settings/tokens (a "Read" token is enough).
    Pass it with -HuggingFaceToken or set $env:HF_TOKEN first.

.PARAMETER ModelsRoot
    Destination folder. Subfolders are created per model.

.PARAMETER LlmVariant
    Which primary LLM to fetch: qwen3-30b-a3b (default, Qwen's official
    unquantized Instruct-2507 weights, ~60GB, needs multi-GPU or an 80GB
    card), qwen3-30b-a3b-awq (RedHat AI's w4a16 quantization of the same
    Instruct-2507 checkpoint, ~16GB, fits a single 24GB card), qwen3-14b or
    gemma-3-12b (smaller fallbacks per the handoff's model table).

.PARAMETER SkipVideo
    Skip Wan2.2-TI2V-5B (~11GB). Only needed for video-mode packages.

.PARAMETER SkipLipsync
    Skip LatentSync/MuseTalk/SadTalker (~15GB total). Only needed for
    face/avatar mode.

.PARAMETER PiperVoiceFa
    Which Persian Piper voice to fetch from rhasspy/piper-voices, as
    "<name>-<quality>" (default amir-medium). Pass "all" to fetch every
    Persian voice.

.PARAMETER HuggingFaceToken
    HF access token, needed for the gated black-forest-labs/FLUX.1-schnell
    VAE. Defaults to $env:HF_TOKEN.

.EXAMPLE
    .\download-models.ps1 -ModelsRoot D:\models -HuggingFaceToken hf_xxx

.EXAMPLE
    .\download-models.ps1 -LlmVariant qwen3-30b-a3b-awq -SkipVideo -SkipLipsync
#>

[CmdletBinding()]
param(
    [string]$ModelsRoot = ".\models",

    [ValidateSet("qwen3-30b-a3b", "qwen3-30b-a3b-awq", "qwen3-14b", "gemma-3-12b")]
    [string]$LlmVariant = "qwen3-30b-a3b",

    [switch]$SkipVideo,
    [switch]$SkipLipsync,

    [string]$PiperVoiceFa = "amir-medium",

    [string]$HuggingFaceToken = $env:HF_TOKEN
)

$ErrorActionPreference = "Stop"

function Assert-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name is required but was not found on PATH. Install Python 3.9+ first: https://www.python.org/downloads/"
    }
}

function Ensure-HuggingFaceCli {
    if (Get-Command huggingface-cli -ErrorAction SilentlyContinue) {
        return
    }
    Write-Host "Installing huggingface_hub[cli] via pip..." -ForegroundColor Cyan
    python -m pip install --upgrade "huggingface_hub[cli]" | Out-Null
    if (-not (Get-Command huggingface-cli -ErrorAction SilentlyContinue)) {
        throw "huggingface-cli still not on PATH after install. Open a new shell (PATH refresh) and re-run."
    }
}

# repo:    HuggingFace repo id
# dest:    subfolder under $ModelsRoot
# include: optional array of --include glob patterns (skip to get the whole repo)
# gated:   true if the repo requires accepting terms on huggingface.co first
function Get-HfModel {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][string]$Repo,
        [Parameter(Mandatory)][string]$Dest,
        [string[]]$Include,
        [switch]$Gated
    )

    $destPath = Join-Path $ModelsRoot $Dest
    New-Item -ItemType Directory -Force -Path $destPath | Out-Null

    Write-Host ""
    Write-Host "==> $Label ($Repo)" -ForegroundColor Green
    if ($Gated -and -not $HuggingFaceToken) {
        Write-Warning "$Repo is gated. Accept its license at https://huggingface.co/$Repo and pass -HuggingFaceToken, or this download will fail."
    }

    $cliArgs = @("download", $Repo, "--local-dir", $destPath, "--local-dir-use-symlinks", "False")
    if ($Include) {
        foreach ($pattern in $Include) {
            $cliArgs += @("--include", $pattern)
        }
    }
    if ($HuggingFaceToken) {
        $cliArgs += @("--token", $HuggingFaceToken)
    }

    & huggingface-cli @cliArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "$Label FAILED (exit $LASTEXITCODE) - see message above. Continuing with the rest."
        $script:Failures += $Label
    } else {
        $script:Succeeded += $Label
    }
}

Assert-Command python
Ensure-HuggingFaceCli
New-Item -ItemType Directory -Force -Path $ModelsRoot | Out-Null

$script:Succeeded = @()
$script:Failures = @()

Write-Host "Downloading phase-0 models into: $((Resolve-Path $ModelsRoot).Path)" -ForegroundColor Cyan
if (-not $HuggingFaceToken) {
    Write-Warning "No HF token set (-HuggingFaceToken or `$env:HF_TOKEN). Gated repos (FLUX VAE) will fail without one."
}

# ---------------------------------------------------------------------------
# 1. LLM (choose ONE via -LlmVariant; this is a phase-0 sizing decision,
#    app/services/tuning.py re-validates whichever one is actually loaded)
# ---------------------------------------------------------------------------
switch ($LlmVariant) {
    "qwen3-30b-a3b" {
        # Instruct-2507, not the original Qwen3-30B-A3B: Qwen's own July 2025
        # refresh, non-thinking only (no <think> blocks to strip from agent
        # output) with meaningfully better instruction-following, alignment
        # and long-context handling per Qwen's own release notes - a better
        # fit for this platform's agents than the original release.
        Get-HfModel -Label "LLM: Qwen3-30B-A3B-Instruct-2507 (official, unquantized bf16, ~60GB)" `
            -Repo "Qwen/Qwen3-30B-A3B-Instruct-2507" -Dest "llm\qwen3-30b-a3b"
    }
    "qwen3-30b-a3b-awq" {
        # RedHat AI (formerly Neural Magic) wrote and maintains llm-compressor,
        # the vLLM project's own quantization tool, and publishes their w4a16
        # compressed-tensors format (vLLM's native quantized format) rather
        # than a raw AWQ checkpoint - not a Qwen release, but not an
        # unaudited third-party mirror either. Apache-2.0, same as the base
        # model (decision D6).
        Get-HfModel -Label "LLM: Qwen3-30B-A3B-Instruct-2507 quantized.w4a16 (RedHat AI, ~16GB)" `
            -Repo "RedHatAI/Qwen3-30B-A3B-Instruct-2507-quantized.w4a16" -Dest "llm\qwen3-30b-a3b-awq"
    }
    "qwen3-14b" {
        Get-HfModel -Label "LLM: Qwen3-14B (official, ~28GB)" `
            -Repo "Qwen/Qwen3-14B" -Dest "llm\qwen3-14b"
    }
    "gemma-3-12b" {
        Write-Warning "google/gemma-3-12b-it requires accepting Google's Gemma license on the model page before it will download."
        Get-HfModel -Label "LLM: Gemma-3-12B-it (official, ~24GB, gated)" `
            -Repo "google/gemma-3-12b-it" -Dest "llm\gemma-3-12b-it" -Gated
    }
}

# ---------------------------------------------------------------------------
# 2. Embedding model
# ---------------------------------------------------------------------------
Get-HfModel -Label "Embedding: BGE-M3" `
    -Repo "BAAI/bge-m3" -Dest "embedding\bge-m3"

# ---------------------------------------------------------------------------
# 3. Image generation: FLUX.1-schnell (fp8 UNet + text encoders + VAE)
# ---------------------------------------------------------------------------
Get-HfModel -Label "Image: FLUX.1-schnell fp8 UNet (~17GB)" `
    -Repo "Comfy-Org/flux1-schnell" -Dest "image\flux1-schnell" `
    -Include @("flux1-schnell-fp8.safetensors")

Get-HfModel -Label "Image: FLUX text encoders (clip_l + t5xxl fp8)" `
    -Repo "comfyanonymous/flux_text_encoders" -Dest "image\flux-text-encoders" `
    -Include @("clip_l.safetensors", "t5xxl_fp8_e4m3fn.safetensors")

Get-HfModel -Label "Image: FLUX.1-schnell VAE (official, gated)" `
    -Repo "black-forest-labs/FLUX.1-schnell" -Dest "image\flux1-schnell-vae" `
    -Include @("ae.safetensors") -Gated

# ---------------------------------------------------------------------------
# 4. Video generation
# ---------------------------------------------------------------------------
if (-not $SkipVideo) {
    Get-HfModel -Label "Video: Wan2.2-TI2V-5B (~11GB)" `
        -Repo "Wan-AI/Wan2.2-TI2V-5B" -Dest "video\wan2.2-ti2v-5b"
} else {
    Write-Host "Skipping video model (-SkipVideo)." -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# 5. Transcription (faster-whisper, CTranslate2 format)
# ---------------------------------------------------------------------------
Get-HfModel -Label "Whisper: faster-whisper-large-v3" `
    -Repo "Systran/faster-whisper-large-v3" -Dest "whisper\large-v3"

Get-HfModel -Label "Whisper: faster-whisper-medium (fallback / cheaper)" `
    -Repo "Systran/faster-whisper-medium" -Dest "whisper\medium"

# ---------------------------------------------------------------------------
# 6. TTS: Chatterbox Multilingual (EN/AR + 21 other languages) and
#    Piper voices for Persian (espeak-ng already covers a real offline
#    fallback with no model download at all - see docs/voice-video.md)
# ---------------------------------------------------------------------------
Get-HfModel -Label "TTS: Chatterbox Multilingual" `
    -Repo "ResembleAI/chatterbox" -Dest "tts\chatterbox"

if ($PiperVoiceFa -eq "all") {
    Get-HfModel -Label "TTS: Piper voices (ALL Persian voices)" `
        -Repo "rhasspy/piper-voices" -Dest "tts\piper-voices" `
        -Include @("fa/**")
} else {
    Get-HfModel -Label "TTS: Piper voice fa_IR-$PiperVoiceFa" `
        -Repo "rhasspy/piper-voices" -Dest "tts\piper-voices" `
        -Include @("fa/fa_IR/*/*/fa_IR-$PiperVoiceFa.onnx", "fa/fa_IR/*/*/fa_IR-$PiperVoiceFa.onnx.json")
}

# ---------------------------------------------------------------------------
# 7. Lip-sync / talking-head (face/avatar video mode)
# ---------------------------------------------------------------------------
if (-not $SkipLipsync) {
    Get-HfModel -Label "Lipsync: LatentSync 1.5 (~10GB)" `
        -Repo "ByteDance/LatentSync-1.5" -Dest "lipsync\latentsync-1.5"

    Get-HfModel -Label "Lipsync: MuseTalk (alternative)" `
        -Repo "TMElyralab/MuseTalk" -Dest "lipsync\musetalk"

    Get-HfModel -Label "Lipsync: SadTalker (alternative)" `
        -Repo "vinthony/SadTalker" -Dest "lipsync\sadtalker"
} else {
    Write-Host "Skipping lip-sync models (-SkipLipsync)." -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "===================== SUMMARY =====================" -ForegroundColor Cyan
Write-Host "Succeeded ($($script:Succeeded.Count)):" -ForegroundColor Green
$script:Succeeded | ForEach-Object { Write-Host "  - $_" }
if ($script:Failures.Count -gt 0) {
    Write-Host "Failed ($($script:Failures.Count)):" -ForegroundColor Red
    $script:Failures | ForEach-Object { Write-Host "  - $_" }
    Write-Host ""
    Write-Host "Re-run this script to retry only what's missing - huggingface-cli resumes/skips already-downloaded files." -ForegroundColor Yellow
} else {
    Write-Host "All models downloaded." -ForegroundColor Green
}
Write-Host ""
Write-Host "Copy the '$ModelsRoot' folder onto the GPU host and point each backend's *_MODEL_PATH env var at it (see docs/HANDOFF.md and docs/voice-video.md)." -ForegroundColor Cyan
