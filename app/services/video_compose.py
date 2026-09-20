"""Muxing a still image, narration and subtitles into a video file.

Always real, never simulated — the same reasoning as
:mod:`app.services.image_overlay` and :mod:`app.services.image_compose`:
``ffmpeg`` is a deterministic CPU tool, not a model with weights phase 0 has
to validate, so there is nothing here for a placeholder to stand in for.
Handoff section 4 lists ``ffmpeg`` on the ``media_cpu`` queue for exactly
this job.

``video_mode="voice"`` gets a still frame with narration and burned-in
captions — no motion, which is the entire difference between ``voice`` and
``face`` (handoff section 8; ``face`` is phase 3 and needs a real or
AI-generated moving face, not just a picture).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass


class VideoComposeError(RuntimeError):
    """The video could not be produced."""


@dataclass
class VideoResult:
    video_bytes: bytes
    duration_seconds: float
    width: int
    height: int


def _run(cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, capture_output=True, check=True)  # noqa: S603
    except FileNotFoundError as exc:
        raise VideoComposeError(f"{cmd[0]!r} is not installed") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (
            exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else exc.stderr
        )
        raise VideoComposeError(f"{cmd[0]} failed: {stderr[-2000:]}") from exc


def _probe_duration(path: str, ffprobe_binary: str) -> float:
    try:
        result = subprocess.run(  # noqa: S603
            [
                ffprobe_binary,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            check=True,
            text=True,
        )
        return float(result.stdout.strip())
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError):
        return 0.0


#: ffmpeg's ``subtitles`` filter reads a path off disk, not a byte string —
#: the caret and colon in an absolute Windows-style path would need escaping
#: too, but this platform only ever runs on POSIX, so a plain filter-graph
#: quote is enough.
def _escape_for_filter(path: str) -> str:
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def mux_voice_video(
    image_bytes: bytes,
    audio_bytes: bytes,
    srt_bytes: bytes | None,
    *,
    width: int,
    height: int,
    ffmpeg_binary: str = "ffmpeg",
    ffprobe_binary: str = "ffprobe",
) -> VideoResult:
    """A still ``image_bytes`` held for the length of ``audio_bytes``, with
    ``srt_bytes`` burned in as open captions when given."""
    if shutil.which(ffmpeg_binary) is None:
        raise VideoComposeError(f"{ffmpeg_binary!r} is not on PATH")

    with tempfile.TemporaryDirectory(prefix="video-mux-") as tmp:
        image_path = os.path.join(tmp, "background.png")
        audio_path = os.path.join(tmp, "narration.wav")
        output_path = os.path.join(tmp, "output.mp4")
        with open(image_path, "wb") as handle:
            handle.write(image_bytes)
        with open(audio_path, "wb") as handle:
            handle.write(audio_bytes)

        video_filters = [f"scale={width}:{height}:force_original_aspect_ratio=decrease",
                          f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"]
        if srt_bytes:
            srt_path = os.path.join(tmp, "captions.srt")
            with open(srt_path, "wb") as handle:
                handle.write(srt_bytes)
            video_filters.append(
                f"subtitles={_escape_for_filter(srt_path)}:"
                "force_style='FontSize=22,Outline=2,Alignment=2,MarginV=40'"
            )
        video_filters.append("format=yuv420p")

        _run(
            [
                ffmpeg_binary,
                "-y",
                "-loop",
                "1",
                "-i",
                image_path,
                "-i",
                audio_path,
                "-vf",
                ",".join(video_filters),
                "-c:v",
                "libx264",
                "-tune",
                "stillimage",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-shortest",
                "-movflags",
                "+faststart",
                output_path,
            ]
        )

        duration = _probe_duration(output_path, ffprobe_binary)
        with open(output_path, "rb") as handle:
            video_bytes = handle.read()

    return VideoResult(
        video_bytes=video_bytes, duration_seconds=duration, width=width, height=height
    )
