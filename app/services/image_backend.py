"""Generating the base image itself — the part phase 0 has not built yet.

Same three-way split as :mod:`app.agents.llm` and
:mod:`app.worker.gpu_runtime`, and for the same reason: nothing here can be
real until phase 0 has measured FLUX.1-schnell on the actual card (handoff
section 11), but the queue, the overlay, the compositor, the storage and the
gate around it all can be built and tested today.

``ComfyUIImageBackend``
    The real one. ComfyUI's HTTP API takes a *workflow graph*, not a prompt
    string — the graph for FLUX.1-schnell is a phase 0 deliverable, produced
    once the model is running and its VRAM/latency profile is known, so this
    fails loudly rather than guess at a JSON shape ahead of that.
``SimulatedImageBackend``
    A deterministic placeholder from :mod:`app.services.image_compose`,
    reporting a plausible duration so the quota mechanism sees realistic
    numbers even with no model present.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Protocol

from app.db.enums import GpuJobKind
from app.services import image_compose
from app.services.quota import DEFAULT_ESTIMATES


class ImageGenerationError(RuntimeError):
    """The image could not be produced."""


@dataclass
class ImageResult:
    png_bytes: bytes
    width: int
    height: int
    gpu_seconds: float


class ImageBackend(Protocol):
    def generate(
        self,
        prompt: str,
        *,
        width: int,
        height: int,
        negative_prompt: str | None = None,
        seed: int | None = None,
    ) -> ImageResult: ...


class ComfyUIImageBackend:
    """Talks to a ComfyUI server. Not implemented until phase 0 delivers the
    FLUX.1-schnell workflow graph."""

    _MESSAGE = (
        "no real image backend is configured. Phase 0 must first run "
        "FLUX.1-schnell on ComfyUI and export its workflow graph (handoff "
        "section 11). Set IMAGE_BACKEND=simulated to run the platform "
        "without models."
    )

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url

    def generate(self, prompt: str, **_: object) -> ImageResult:
        raise NotImplementedError(self._MESSAGE)


class SimulatedImageBackend:
    """A deterministic placeholder, standing in for FLUX.1-schnell.

    ``speedup`` mirrors :class:`~app.worker.gpu_runtime.SimulatedGpuRuntime`:
    at the default the reported duration stays realistic for the quota
    mechanism while the actual wall-clock wait stays short enough for a demo
    or a test run.
    """

    def __init__(self, speedup: float = 600.0, seed: int | None = None) -> None:
        self.speedup = max(1.0, speedup)
        self._random = random.Random(seed)

    def generate(
        self,
        prompt: str,
        *,
        width: int,
        height: int,
        negative_prompt: str | None = None,
        seed: int | None = None,
    ) -> ImageResult:
        base_seconds = DEFAULT_ESTIMATES[GpuJobKind.IMAGE_FLUX]
        seconds = base_seconds * self._random.uniform(0.75, 1.25)
        time.sleep(seconds / self.speedup)

        # A real diffusion model produces a different image per seed even
        # from an identical prompt — that is the entire reason a gallery of
        # several options is worth generating. simulated_base_image is
        # otherwise pure a function of its text, so the seed is folded into
        # that text rather than the prompt alone, or every option in a
        # gallery would render as the same picture.
        # Palette is not passed through here — the compositor call in the
        # executor already has it from the visual brief and applies it to
        # the placeholder directly, keeping this backend's signature the one
        # a real ComfyUI adapter will actually have.
        keyed_prompt = f"{prompt}\x00seed={seed}" if seed is not None else prompt
        png = image_compose.simulated_base_image(keyed_prompt, width, height)
        return ImageResult(png_bytes=png, width=width, height=height, gpu_seconds=round(seconds, 2))


_backend: ImageBackend | None = None


def build_backend(name: str | None = None) -> ImageBackend:
    from app.core.config import settings

    name = name or settings.image_backend
    if name == "simulated":
        return SimulatedImageBackend(speedup=settings.gpu_simulation_speedup)
    return ComfyUIImageBackend()


def get_backend() -> ImageBackend:
    global _backend
    if _backend is None:
        _backend = build_backend()
    return _backend


def set_backend(backend: ImageBackend | None) -> None:
    """Test hook, and how the worker installs a warmed backend."""
    global _backend
    _backend = backend
