"""Writing an SRT file from narration timing.

No transcription step here, on purpose. The handoff's own plan runs Whisper
over the finished narration to time captions (section 8), which makes sense
when the voice track comes from a model whose output you have not seen
before. Ours does not: :mod:`app.services.tts_backend` synthesizes one
section at a time and already knows both the exact text and the exact
duration of each piece of audio it produced, which is strictly better ground
truth than re-transcribing our own speech would give back. Whisper stays the
plan for whatever eventually needs to time a *human-recorded* track (the
``face`` mode profile upload in phase 3); it has nothing to add here.
"""

from __future__ import annotations

from app.services.tts_backend import TtsSegment

#: A caption on screen for less than this reads as a flash, not a subtitle.
_MIN_CUE_SECONDS = 1.0


def _timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    whole = int(seconds)
    millis = round((seconds - whole) * 1000)
    if millis == 1000:  # rounding carried into the next second
        whole += 1
        millis = 0
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def to_srt(segments: list[TtsSegment]) -> bytes:
    """One cue per narrated section, in SubRip format."""
    lines: list[str] = []
    for index, segment in enumerate(segments, start=1):
        end = max(segment.end_seconds, segment.start_seconds + _MIN_CUE_SECONDS)
        lines.append(str(index))
        lines.append(f"{_timestamp(segment.start_seconds)} --> {_timestamp(end)}")
        lines.append(segment.text)
        lines.append("")
    return ("\n".join(lines)).encode("utf-8")
