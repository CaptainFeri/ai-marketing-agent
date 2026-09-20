"""The FLUX stand-in and the real-backend placeholder.

Same pattern as ``tests/test_agent_runner.py`` for the LLM client: the
simulated backend is exercised for real, and the not-yet-implemented real one
is checked to fail with a message that says why, and how to work without it.
"""

from __future__ import annotations

import pytest

from app.db.enums import GpuJobKind
from app.services.image_backend import (
    ComfyUIImageBackend,
    ImageGenerationError,
    SimulatedImageBackend,
    build_backend,
    get_backend,
    set_backend,
)
from app.services.quota import DEFAULT_ESTIMATES


@pytest.fixture(autouse=True)
def _reset_backend():
    yield
    set_backend(None)


def test_the_simulated_backend_returns_the_requested_size() -> None:
    backend = SimulatedImageBackend(speedup=1_000_000, seed=1)
    result = backend.generate("a drill", width=256, height=256)
    assert result.width == 256
    assert result.height == 256
    assert result.png_bytes.startswith(b"\x89PNG")


def test_the_simulated_backend_reports_a_plausible_duration() -> None:
    """Zero seconds would leave the quota mechanism untested in a demo run,
    the same reasoning as the simulated LLM client."""
    backend = SimulatedImageBackend(speedup=1_000_000, seed=1)
    result = backend.generate("a drill", width=256, height=256)
    seed_estimate = DEFAULT_ESTIMATES[GpuJobKind.IMAGE_FLUX]
    assert result.gpu_seconds == pytest.approx(seed_estimate, rel=0.3)


def test_a_negative_prompt_and_seed_do_not_raise() -> None:
    backend = SimulatedImageBackend(speedup=1_000_000, seed=1)
    result = backend.generate(
        "a drill", width=128, height=128, negative_prompt="blurry, text", seed=7
    )
    assert result.width == 128


def test_the_real_backend_fails_loudly_with_a_way_around_it() -> None:
    """A misconfigured server that expected real models should stop, not
    quietly emit placeholder content — same rule as GpuRuntime and LlmClient."""
    backend = ComfyUIImageBackend()
    with pytest.raises(NotImplementedError) as excinfo:
        backend.generate("a drill", width=256, height=256)
    assert "IMAGE_BACKEND=simulated" in str(excinfo.value)
    assert "Phase 0" in str(excinfo.value)


def test_generation_error_is_the_common_failure_type() -> None:
    """Not asserted elsewhere, but the type exists for a real backend's
    network/API failures to raise as, same shape as LlmError."""
    assert issubclass(ImageGenerationError, RuntimeError)


# ---------------------------------------------------------------------------
# choosing a backend
# ---------------------------------------------------------------------------
def test_build_backend_defaults_to_simulated() -> None:
    assert isinstance(build_backend("simulated"), SimulatedImageBackend)


def test_build_backend_picks_comfyui_for_anything_else() -> None:
    assert isinstance(build_backend("comfyui"), ComfyUIImageBackend)


def test_get_backend_is_a_singleton_until_reset() -> None:
    set_backend(None)
    first = get_backend()
    second = get_backend()
    assert first is second


def test_set_backend_overrides_the_singleton() -> None:
    custom = SimulatedImageBackend(seed=99)
    set_backend(custom)
    assert get_backend() is custom
