"""Muxing a still image, narration and captions into a video — real
``ffmpeg``, always, the same posture as ``tests/test_image_overlay.py``
takes toward real Playwright."""

from __future__ import annotations

import io
import math
import shutil
import struct
import wave

import pytest
from PIL import Image

from app.services.video_compose import VideoComposeError, mux_voice_video

ffmpeg_missing = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg is not installed"
)


def _png_bytes(width: int = 64, height: int = 64) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (20, 40, 80)).save(buffer, format="PNG")
    return buffer.getvalue()


def _wav_bytes(seconds: float = 1.0, rate: int = 16000) -> bytes:
    """A real, decodable sine-tone WAV — no external tool needed to build
    the fixture, only to consume it."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = int(seconds * rate)
        samples = [
            int(3000 * math.sin(2 * math.pi * 220 * (i / rate))) for i in range(frames)
        ]
        handle.writeframes(struct.pack(f"<{frames}h", *samples))
    return buffer.getvalue()


@ffmpeg_missing
def test_mux_produces_a_playable_mp4_of_the_audio_length() -> None:
    result = mux_voice_video(_png_bytes(), _wav_bytes(seconds=1.5), None, width=128, height=128)

    assert result.video_bytes.startswith(b"\x00\x00\x00")  # an ISO-BMFF/mp4 box header
    assert result.duration_seconds == pytest.approx(1.5, abs=0.2)
    assert result.width == 128
    assert result.height == 128


@ffmpeg_missing
def test_mux_with_subtitles_still_produces_a_video() -> None:
    srt = (
        "1\n00:00:00,000 --> 00:00:01,000\nسلام دنیا\n\n"
    ).encode()
    result = mux_voice_video(_png_bytes(), _wav_bytes(seconds=1.0), srt, width=128, height=128)
    assert len(result.video_bytes) > 0
    assert result.duration_seconds > 0


@ffmpeg_missing
def test_a_non_square_image_is_letterboxed_to_the_requested_size() -> None:
    wide_png = _png_bytes(width=200, height=50)
    result = mux_voice_video(wide_png, _wav_bytes(seconds=0.5), None, width=128, height=128)
    assert result.width == 128
    assert result.height == 128


def test_a_missing_binary_fails_clearly() -> None:
    with pytest.raises(VideoComposeError, match="not on PATH"):
        mux_voice_video(
            _png_bytes(),
            _wav_bytes(),
            None,
            width=64,
            height=64,
            ffmpeg_binary="not-a-real-ffmpeg-binary",
        )


@ffmpeg_missing
def test_garbage_audio_input_fails_as_a_clear_error() -> None:
    with pytest.raises(VideoComposeError):
        mux_voice_video(_png_bytes(), b"not a real wav file", None, width=64, height=64)
