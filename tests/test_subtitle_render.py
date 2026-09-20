"""SRT generation from narration timing — pure formatting, no model or
subprocess involved, unlike the rest of the voice pipeline."""

from __future__ import annotations

from app.services.subtitle_render import to_srt
from app.services.tts_backend import TtsSegment


def test_one_segment_becomes_one_numbered_cue() -> None:
    srt = to_srt([TtsSegment(text="سلام دنیا", start_seconds=0.0, end_seconds=2.5)]).decode()
    assert srt.startswith("1\n00:00:00,000 --> 00:00:02,500\nسلام دنیا")


def test_segments_are_numbered_in_order() -> None:
    segments = [
        TtsSegment(text="اول", start_seconds=0.0, end_seconds=1.0),
        TtsSegment(text="دوم", start_seconds=1.0, end_seconds=2.0),
        TtsSegment(text="سوم", start_seconds=2.0, end_seconds=3.0),
    ]
    srt = to_srt(segments).decode()
    assert srt.split("\n")[0] == "1"
    assert "\n2\n" in srt
    assert "\n3\n" in srt


def test_a_cue_shorter_than_the_minimum_is_stretched() -> None:
    """A caption on screen for a tenth of a second reads as a flash — every
    cue gets at least a floor duration regardless of how short its audio
    segment actually ran."""
    srt = to_srt([TtsSegment(text="x", start_seconds=0.0, end_seconds=0.1)]).decode()
    assert "00:00:00,000 --> 00:00:01,000" in srt


def test_timestamps_carry_hours_and_minutes_correctly() -> None:
    srt = to_srt(
        [TtsSegment(text="late", start_seconds=3725.25, end_seconds=3730.0)]
    ).decode()
    assert "01:02:05,250 --> 01:02:10,000" in srt


def test_no_segments_produces_an_empty_file() -> None:
    assert to_srt([]) == b""
