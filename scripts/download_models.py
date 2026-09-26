#!/usr/bin/env python3
"""Downloads the phase-0 model weights the platform needs (docs/HANDOFF.md
section on required models), so they can be copied onto the GPU host that
will actually run them.

Python equivalent of download-models.ps1, same behavior and same output
layout under --models-root (matching what scripts/check_models.py expects:
llm/, embedding/, image/, video/, whisper/, tts/, lipsync/) - for anyone
who'd rather not install PowerShell for this.

Run this on any machine with internet access to huggingface.co. It never
touches the app itself - it only populates a local folder tree. Uses
huggingface_hub's own snapshot_download, so downloads are resumable and
content-addressed; re-running this script only fetches what's missing or
changed.

Some repos are gated on huggingface.co (you must click "Agree" on the model
page once while logged in) and/or need a token:
https://huggingface.co/settings/tokens (a "Read" token is enough). Pass it
with --hf-token or set $HF_TOKEN first.

    python scripts/download_models.py
    python scripts/download_models.py --models-root /mnt/d/models --hf-token hf_xxx
    python scripts/download_models.py --llm-variant qwen3-30b-a3b-awq --skip-video --skip-lipsync
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


def _ensure_huggingface_hub() -> None:
    try:
        import huggingface_hub  # noqa: F401

        return
    except ImportError:
        pass
    print("Installing huggingface_hub via pip...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "--quiet", "huggingface_hub"],
        check=True,
    )


@dataclass
class Job:
    label: str
    repo: str
    dest: str  # relative to --models-root, forward slashes
    allow_patterns: list[str] | None = None
    gated: bool = False


def _download_one(
    job: Job, models_root: Path, hf_token: str | None, succeeded: list[str], failed: list[str]
) -> None:
    from huggingface_hub import snapshot_download

    dest_path = models_root / job.dest
    dest_path.mkdir(parents=True, exist_ok=True)

    print()
    print(f"==> {job.label} ({job.repo})")
    if job.gated and not hf_token:
        print(
            f"WARNING: {job.repo} is gated. Accept its license at "
            f"https://huggingface.co/{job.repo} and pass --hf-token, "
            "or this download will fail."
        )

    try:
        snapshot_download(
            repo_id=job.repo,
            local_dir=str(dest_path),
            allow_patterns=job.allow_patterns,
            token=hf_token,
        )
    except Exception as exc:  # noqa: BLE001 - any failure (HTTP, proxy,
        # timeout, ...) should be recorded and skipped over, matching
        # download-models.ps1's "continuing with the rest" posture, not
        # crash the whole run over one repo.
        print(f"WARNING: {job.label} FAILED ({exc}) - continuing with the rest.")
        failed.append(job.label)
    else:
        succeeded.append(job.label)


def build_jobs(llm_variant: str, piper_voice_fa: str) -> list[Job]:
    jobs: list[Job] = []

    # 1. LLM (choose ONE via --llm-variant; this is a phase-0 sizing
    #    decision, app/services/tuning.py re-validates whichever one is
    #    actually loaded)
    if llm_variant == "qwen3-30b-a3b":
        # Instruct-2507, not the original Qwen3-30B-A3B: Qwen's own July
        # 2025 refresh, non-thinking only (no <think> blocks to strip from
        # agent output) with meaningfully better instruction-following,
        # alignment and long-context handling per Qwen's own release notes.
        jobs.append(
            Job(
                "LLM: Qwen3-30B-A3B-Instruct-2507 (official, unquantized bf16, ~60GB)",
                "Qwen/Qwen3-30B-A3B-Instruct-2507",
                "llm/qwen3-30b-a3b",
            )
        )
    elif llm_variant == "qwen3-30b-a3b-awq":
        # RedHat AI (formerly Neural Magic) wrote and maintains
        # llm-compressor, the vLLM project's own quantization tool, and
        # publishes their w4a16 compressed-tensors format (vLLM's native
        # quantized format) - not a Qwen release, but not an unaudited
        # third-party mirror either. Apache-2.0, same as the base model
        # (decision D6).
        jobs.append(
            Job(
                "LLM: Qwen3-30B-A3B-Instruct-2507 quantized.w4a16 (RedHat AI, ~16GB)",
                "RedHatAI/Qwen3-30B-A3B-Instruct-2507-quantized.w4a16",
                "llm/qwen3-30b-a3b-awq",
            )
        )
    elif llm_variant == "qwen3-14b":
        jobs.append(Job("LLM: Qwen3-14B (official, ~28GB)", "Qwen/Qwen3-14B", "llm/qwen3-14b"))
    elif llm_variant == "gemma-3-12b":
        print(
            "WARNING: google/gemma-3-12b-it requires accepting Google's "
            "Gemma license on the model page before it will download."
        )
        jobs.append(
            Job(
                "LLM: Gemma-3-12B-it (official, ~24GB, gated)",
                "google/gemma-3-12b-it",
                "llm/gemma-3-12b-it",
                gated=True,
            )
        )

    # 2. Embedding model
    jobs.append(Job("Embedding: BGE-M3", "BAAI/bge-m3", "embedding/bge-m3"))

    # 3. Image generation: FLUX.1-schnell (fp8 UNet + text encoders + VAE)
    jobs.append(
        Job(
            "Image: FLUX.1-schnell fp8 UNet (~17GB)",
            "Comfy-Org/flux1-schnell",
            "image/flux1-schnell",
            allow_patterns=["flux1-schnell-fp8.safetensors"],
        )
    )
    jobs.append(
        Job(
            "Image: FLUX text encoders (clip_l + t5xxl fp8)",
            "comfyanonymous/flux_text_encoders",
            "image/flux-text-encoders",
            allow_patterns=["clip_l.safetensors", "t5xxl_fp8_e4m3fn.safetensors"],
        )
    )
    jobs.append(
        Job(
            "Image: FLUX.1-schnell VAE (official, gated)",
            "black-forest-labs/FLUX.1-schnell",
            "image/flux1-schnell-vae",
            allow_patterns=["ae.safetensors"],
            gated=True,
        )
    )

    # 4. Video generation - added conditionally by main() (--skip-video)

    # 5. Transcription (faster-whisper, CTranslate2 format)
    jobs.append(
        Job(
            "Whisper: faster-whisper-large-v3",
            "Systran/faster-whisper-large-v3",
            "whisper/large-v3",
        )
    )
    jobs.append(
        Job(
            "Whisper: faster-whisper-medium (fallback / cheaper)",
            "Systran/faster-whisper-medium",
            "whisper/medium",
        )
    )

    # 6. TTS: Chatterbox Multilingual (EN/AR + 21 other languages) and
    #    Piper voices for Persian (espeak-ng already covers a real offline
    #    fallback with no model download at all - see docs/voice-video.md)
    jobs.append(
        Job("TTS: Chatterbox Multilingual", "ResembleAI/chatterbox", "tts/chatterbox")
    )
    if piper_voice_fa == "all":
        jobs.append(
            Job(
                "TTS: Piper voices (ALL Persian voices)",
                "rhasspy/piper-voices",
                "tts/piper-voices",
                allow_patterns=["fa/**"],
            )
        )
    else:
        jobs.append(
            Job(
                f"TTS: Piper voice fa_IR-{piper_voice_fa}",
                "rhasspy/piper-voices",
                "tts/piper-voices",
                allow_patterns=[
                    f"fa/fa_IR/*/*/fa_IR-{piper_voice_fa}.onnx",
                    f"fa/fa_IR/*/*/fa_IR-{piper_voice_fa}.onnx.json",
                ],
            )
        )

    return jobs


def build_lipsync_jobs() -> list[Job]:
    return [
        Job(
            "Lipsync: LatentSync 1.5 (~10GB)",
            "ByteDance/LatentSync-1.5",
            "lipsync/latentsync-1.5",
        ),
        Job("Lipsync: MuseTalk (alternative)", "TMElyralab/MuseTalk", "lipsync/musetalk"),
        Job("Lipsync: SadTalker (alternative)", "vinthony/SadTalker", "lipsync/sadtalker"),
    ]


def build_all_jobs(
    llm_variant: str, piper_voice_fa: str, *, skip_video: bool, skip_lipsync: bool
) -> list[Job]:
    """Full job list for one run - the single source of truth for what
    --skip-video/--skip-lipsync do, shared by main() and by tests so they
    can't drift apart the way build_jobs() and main() once did."""
    jobs = build_jobs(llm_variant, piper_voice_fa)
    if not skip_video:
        jobs.append(
            Job("Video: Wan2.2-TI2V-5B (~11GB)", "Wan-AI/Wan2.2-TI2V-5B", "video/wan2.2-ti2v-5b")
        )
    if not skip_lipsync:
        jobs.extend(build_lipsync_jobs())
    return jobs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--models-root", default="./models")
    parser.add_argument(
        "--llm-variant",
        choices=["qwen3-30b-a3b", "qwen3-30b-a3b-awq", "qwen3-14b", "gemma-3-12b"],
        default="qwen3-30b-a3b",
    )
    parser.add_argument("--skip-video", action="store_true")
    parser.add_argument("--skip-lipsync", action="store_true")
    parser.add_argument("--piper-voice-fa", default="amir-medium")
    parser.add_argument("--hf-token", default=os.environ.get("HF_TOKEN"))
    args = parser.parse_args(argv)

    _ensure_huggingface_hub()

    models_root = Path(args.models_root)
    models_root.mkdir(parents=True, exist_ok=True)

    print(f"Downloading phase-0 models into: {models_root.resolve()}")
    if not args.hf_token:
        print(
            "WARNING: No HF token set (--hf-token or $HF_TOKEN). "
            "Gated repos (FLUX VAE) will fail without one."
        )

    if args.skip_video:
        print("Skipping video model (--skip-video).")
    if args.skip_lipsync:
        print("Skipping lip-sync models (--skip-lipsync).")

    jobs = build_all_jobs(
        args.llm_variant,
        args.piper_voice_fa,
        skip_video=args.skip_video,
        skip_lipsync=args.skip_lipsync,
    )

    succeeded: list[str] = []
    failed: list[str] = []
    for job in jobs:
        _download_one(job, models_root, args.hf_token, succeeded, failed)

    print()
    print("===================== SUMMARY =====================")
    print(f"Succeeded ({len(succeeded)}):")
    for label in succeeded:
        print(f"  - {label}")
    if failed:
        print(f"Failed ({len(failed)}):")
        for label in failed:
            print(f"  - {label}")
        print()
        print(
            "Re-run this script to retry only what's missing - "
            "huggingface_hub resumes/skips already-downloaded files."
        )
    else:
        print("All models downloaded.")
    print()
    print(
        f"Copy the '{args.models_root}' folder onto the GPU host and point "
        "MODELS_DIR at it (see docs/HANDOFF.md and docs/voice-video.md)."
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
