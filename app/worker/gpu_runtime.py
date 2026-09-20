"""What actually loads models onto the card.

The scheduler, the leases and the quota accounting around this module are
complete and exercised by the test suite.  The model adapters themselves are
phase 0 work: vLLM sleep/wake timing, the ComfyUI graphs for FLUX and Wan, and
the lip-sync pipelines all have to be measured on the real 3090 Ti before they
are worth writing down (handoff section 11, phase 0).

Until then :class:`SimulatedGpuRuntime` lets the whole platform run end to end
with plausible timings, so the panel, the gates, the queues and the quota
mechanism can be built and demonstrated without the weights present.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Protocol

from app.core.config import settings
from app.db.enums import GpuJobKind, GpuWindow
from app.services.quota import DEFAULT_ESTIMATES

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    gpu_seconds: float
    output: dict = field(default_factory=dict)


class GpuRuntime(Protocol):
    """Owns the card.  Exactly one instance exists, in the ``gpu`` worker."""

    def ensure_window(self, window: GpuWindow, kind: GpuJobKind) -> float:
        """Make ``kind`` runnable, returning the seconds the switch cost."""

    def run(self, kind: GpuJobKind, payload: dict) -> RunResult:
        """Execute one job.  The caller has already ensured the window."""

    def shutdown(self) -> None: ...


class SimulatedGpuRuntime:
    """Runs nothing; sleeps for a fraction of the estimated time.

    ``speedup`` keeps the simulation usable in tests and demos — at the
    default of 600 a thirteen-minute Wan clip takes about a second, while the
    reported ``gpu_seconds`` stay realistic so the quota mechanism is
    exercised with lifelike numbers.
    """

    def __init__(self, speedup: float = 600.0, seed: int | None = None) -> None:
        self.speedup = max(1.0, speedup)
        self._random = random.Random(seed)
        self._current_kind: GpuJobKind | None = None

    def ensure_window(self, window: GpuWindow, kind: GpuJobKind) -> float:
        if self._current_kind == kind:
            return 0.0
        cost = float(settings.gpu_window_switch_seconds)
        logger.info(
            "simulated model swap",
            extra={"from": getattr(self._current_kind, "value", None), "to": kind.value},
        )
        time.sleep(cost / self.speedup)
        self._current_kind = kind
        return cost

    def run(self, kind: GpuJobKind, payload: dict) -> RunResult:
        base = DEFAULT_ESTIMATES[kind]
        # ±25%, so the moving average has something to converge on.
        seconds = base * self._random.uniform(0.75, 1.25)
        time.sleep(seconds / self.speedup)
        return RunResult(
            gpu_seconds=round(seconds, 2),
            output={"simulated": True, "kind": kind.value, "payload_keys": sorted(payload)},
        )

    def shutdown(self) -> None:
        self._current_kind = None


class UnavailableGpuRuntime:
    """The real runtime, before phase 0 has produced it.

    Fails loudly with the reason rather than pretending to work, so a server
    misconfigured to expect real models does not quietly emit placeholder
    content.
    """

    _MESSAGE = (
        "no real GPU runtime is configured. Phase 0 must first validate the "
        "model stack on the 3090 Ti (vLLM + Qwen3, ComfyUI + FLUX.1-schnell "
        "and Wan 2.2, faster-whisper, LatentSync) and record the measured "
        "timings. Set GPU_RUNTIME=simulated to run the platform without models."
    )

    def ensure_window(self, window: GpuWindow, kind: GpuJobKind) -> float:
        raise NotImplementedError(self._MESSAGE)

    def run(self, kind: GpuJobKind, payload: dict) -> RunResult:
        raise NotImplementedError(self._MESSAGE)

    def shutdown(self) -> None:
        return None


_runtime: GpuRuntime | None = None


def get_runtime() -> GpuRuntime:
    """Process-wide runtime.  Only the single ``gpu`` worker ever calls this."""
    global _runtime
    if _runtime is None:
        _runtime = build_runtime()
    return _runtime


def build_runtime(name: str | None = None) -> GpuRuntime:
    name = name or settings.gpu_runtime
    if name == "simulated":
        return SimulatedGpuRuntime(speedup=settings.gpu_simulation_speedup)
    return UnavailableGpuRuntime()


def set_runtime(runtime: GpuRuntime | None) -> None:
    """Test hook; also used by the worker bootstrap to install a real runtime."""
    global _runtime
    _runtime = runtime
