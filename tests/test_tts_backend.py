"""The narration backends: real espeak-ng today, Piper/Chatterbox once phase
0 delivers their voice models — same pattern as ``tests/test_image_backend.py``.
"""

from __future__ import annotations

import shutil
import wave
from io import BytesIO

import pytest

from app.services.tts_backend import (
    ChatterboxTtsBackend,
    EspeakTtsBackend,
    PiperTtsBackend,
    ProductionTtsBackend,
    TtsError,
    build_backend,
    get_backend,
    set_backend,
)

espeak_missing = pytest.mark.skipif(
    shutil.which("espeak-ng") is None, reason="espeak-ng is not installed"
)


@pytest.fixture(autouse=True)
def _reset_backend():
    yield
    set_backend(None)


@espeak_missing
def test_espeak_produces_real_playable_audio() -> None:
    backend = EspeakTtsBackend()
    result = backend.synthesize(["این یک جمله آزمایشی است."], locale="fa")

    assert result.mime_type == "audio/wav"
    assert result.duration_seconds > 0
    with wave.open(BytesIO(result.audio_bytes), "rb") as handle:
        assert handle.getnframes() > 0


@espeak_missing
def test_each_section_becomes_its_own_timed_segment() -> None:
    backend = EspeakTtsBackend()
    result = backend.synthesize(["بخش اول.", "بخش دوم."], locale="fa")

    assert len(result.segments) == 2
    first, second = result.segments
    assert first.start_seconds == 0.0
    assert first.end_seconds == pytest.approx(second.start_seconds)
    assert second.end_seconds == pytest.approx(result.duration_seconds)


@espeak_missing
def test_an_unknown_locale_falls_back_to_english_rather_than_failing() -> None:
    backend = EspeakTtsBackend()
    result = backend.synthesize(["hello there"], locale="xx")
    assert result.duration_seconds > 0


@espeak_missing
def test_no_narratable_text_is_refused() -> None:
    backend = EspeakTtsBackend()
    with pytest.raises(TtsError, match="nothing to narrate"):
        backend.synthesize(["", "   "], locale="fa")


def test_a_missing_binary_fails_clearly() -> None:
    backend = EspeakTtsBackend(binary="not-a-real-espeak-binary")
    with pytest.raises(TtsError, match="not on PATH"):
        backend.synthesize(["hello"], locale="en")


def test_espeak_reports_zero_gpu_seconds() -> None:
    """It never touches the card, so the quota ledger must not charge for
    it — see quota.record_actual's early return on seconds <= 0."""
    if shutil.which("espeak-ng") is None:
        pytest.skip("espeak-ng is not installed")
    backend = EspeakTtsBackend()
    result = backend.synthesize(["salaam"], locale="fa")
    assert result.gpu_seconds == 0.0


def test_piper_fails_loudly_with_a_way_around_it() -> None:
    with pytest.raises(NotImplementedError) as excinfo:
        PiperTtsBackend().synthesize(["سلام"], locale="fa")
    assert "TTS_BACKEND=espeak" in str(excinfo.value)
    assert "Phase 0" in str(excinfo.value)


def test_chatterbox_fails_loudly_with_a_way_around_it() -> None:
    with pytest.raises(NotImplementedError) as excinfo:
        ChatterboxTtsBackend().synthesize(["hello"], locale="en")
    assert "TTS_BACKEND=espeak" in str(excinfo.value)


def test_production_backend_routes_persian_to_piper() -> None:
    backend = ProductionTtsBackend()
    with pytest.raises(NotImplementedError, match="Piper"):
        backend.synthesize(["سلام"], locale="fa")


def test_production_backend_routes_everything_else_to_chatterbox() -> None:
    backend = ProductionTtsBackend()
    with pytest.raises(NotImplementedError, match="Chatterbox"):
        backend.synthesize(["hello"], locale="en")


# ---------------------------------------------------------------------------
# choosing a backend
# ---------------------------------------------------------------------------
def test_build_backend_defaults_to_espeak() -> None:
    assert isinstance(build_backend("espeak"), EspeakTtsBackend)


def test_build_backend_picks_production_explicitly() -> None:
    assert isinstance(build_backend("production"), ProductionTtsBackend)


def test_get_backend_is_a_singleton_until_reset() -> None:
    set_backend(None)
    assert get_backend() is get_backend()


def test_set_backend_overrides_the_singleton() -> None:
    custom = EspeakTtsBackend()
    set_backend(custom)
    assert get_backend() is custom
