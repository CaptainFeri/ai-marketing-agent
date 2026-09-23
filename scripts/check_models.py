#!/usr/bin/env python3
"""Report which model weights are present under MODELS_DIR.

Never downloads anything — that is deliberately a separate, manual step
(``scripts/download-models.ps1``, run on a machine with internet access,
then copied onto this host). This script only checks what is already
there, matching the folder layout that script creates (``llm/``,
``embedding/``, ``image/``, ``video/``, ``whisper/``, ``tts/``,
``lipsync/``).

    python scripts/check_models.py
    MODELS_DIR=/srv/models python scripts/check_models.py
    python scripts/check_models.py --models-dir /srv/models

Used as a ``gpu-worker`` pre-flight step in ``docker-compose.yml``: a
container configured for a backend that loads its weights directly from
this machine's disk (today, only ``TTS_BACKEND=production``) fails fast
with a clear message here rather than only discovering the gap on the
first real job. vLLM and ComfyUI are separate services this repository's
compose file does not run — their weights live on *that* service's own
disk, not necessarily under ``MODELS_DIR`` — so this script reports on
those categories for phase-0 setup convenience but never blocks startup
over them.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path

#: Category -> what it's for, matching download-models.ps1's -Dest paths.
_CATEGORIES: dict[str, str] = {
    "llm": "Qwen3/Gemma — read by a separate vLLM service, not this container",
    "embedding": "BGE-M3 embeddings",
    "image": "FLUX.1-schnell — read by a separate ComfyUI service, not this container",
    "video": "Wan2.2-TI2V-5B — read by a separate service, not this container",
    "whisper": "faster-whisper transcription",
    "tts": "Piper/Chatterbox voices — TTS_BACKEND=production reads this directly",
    "lipsync": "LatentSync/MuseTalk/SadTalker",
}

#: Categories this container loads directly from disk, and therefore should
#: genuinely block startup over when missing, keyed to whether the current
#: environment actually asks for that backend.
_BLOCKING: dict[str, Callable[[dict], bool]] = {
    "tts": lambda env: env.get("TTS_BACKEND", "espeak") == "production",
}


def _has_any_files(path: Path) -> bool:
    return path.is_dir() and any(path.iterdir())


def main(argv: list[str] | None = None, env: dict | None = None) -> int:
    resolved_env: dict = dict(os.environ) if env is None else env
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", default=resolved_env.get("MODELS_DIR", "./models"))
    args = parser.parse_args(argv)

    models_dir = Path(args.models_dir)
    print(f"Model weights directory: {models_dir}")
    if not models_dir.exists():
        print("  (does not exist yet)")

    blocking_missing: list[str] = []
    for category, description in _CATEGORIES.items():
        present = _has_any_files(models_dir / category)
        marker = "present" if present else "missing"
        print(f"  [{marker:7}] {category:10} - {description}")
        if not present and _BLOCKING.get(category, lambda _env: False)(resolved_env):
            blocking_missing.append(category)

    if blocking_missing:
        print()
        print("This container's own configuration needs weights that are missing:")
        for category in blocking_missing:
            print(f"  - {category}")
        print()
        print(
            "This script never downloads them. Run scripts/download-models.ps1 on a "
            f"machine with internet access, then copy its output into {models_dir} "
            "(or point MODELS_DIR elsewhere)."
        )
        return 1

    print()
    print("Nothing this container loads directly is missing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
