"""Choose a configuration from what the server actually has.

Handoff section 12 lists the server's RAM, CPU and disk as an open question,
and section 6 depends on the answer: how the GPU switches between the text and
media windows is decided by whether the LLM's weights fit in host RAM.  Rather
than leave that to a manual reading of the spec sheet, this module probes the
machine and derives the settings.

Three kinds of number appear below, and they are labelled as such wherever
they surface:

``measured``
    Observed by the running system — the switch time the GPU worker recorded.
    Always preferred when available.
``estimated``
    Derived from the probe and the constants in this module.  Good enough to
    start with, wrong in the third digit.
``assumed``
    Planning figures carried over from the handoff, such as 20 hours of daily
    GPU uptime.

Nothing here is applied automatically.  The output is a report and an ``.env``
fragment for a person to review, because a wrong guess about model choice is
expensive and silent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.core.platform import PlatformProbe

# ---------------------------------------------------------------------------
# constants — the judgement calls, gathered in one place
# ---------------------------------------------------------------------------

#: Host RAM held by everything that is not a model: OS, PostgreSQL, Redis,
#: MinIO, the API and the CPU workers.  Measured on a comparable single-server
#: deployment; revisit if the stack grows.
SERVICES_RESERVE_GB = 6.0

#: ComfyUI keeps image and video weights in host RAM between runs so it does
#: not re-read them from disk every time.  This is what usually gets forgotten
#: when sizing RAM for a single-GPU server.
COMFY_HOST_RESIDENT_GB = 8.0

#: Weights alone do not fit: allocator overhead and pinned buffers add to it.
SLEEP_RAM_HEADROOM = 1.15

#: Effective host-to-device throughput on PCIe 4.0 x16. The theoretical figure
#: is ~25 GB/s; sustained pinned-memory transfers land nearer half of that.
PCIE_EFFECTIVE_GBPS = 10.0

#: Conservative sequential read rates, used when the disk was not benchmarked.
NVME_READ_GBPS = 2.0
HDD_READ_GBPS = 0.15

#: Re-reading weights costs more than the raw disk read: safetensors
#: deserialisation, the quantised-layout setup and the host-to-device copy all
#: sit on top of it, and in practice dominate.  A multiplier is a crude model
#: of that, which is exactly why phase 0 has to measure the real number.
LOAD_PATH_FACTOR = 2.5

#: vLLM process start: CUDA context, graph capture, warm-up. Not the weights.
VLLM_ENGINE_INIT_SECONDS = 45.0

#: Fixed cost either side of a sleep/wake cycle.
SLEEP_WAKE_OVERHEAD_SECONDS = 3.0

#: Handoff section 7's planning assumption for daily GPU uptime.
ASSUMED_UPTIME_HOURS = 20.0

#: A batch should be long enough that switching costs at most this share of
#: the day. Below it the card spends its time loading models, not working.
TARGET_SWITCH_OVERHEAD = 0.04

#: VRAM Wan 2.2 TI2V-5B needs for a 720p clip (handoff section 5).
WAN_MIN_VRAM_GB = 20.0


class SwitchStrategy(StrEnum):
    """How the card gets from the text window to the media window."""

    #: Two or more GPUs: one holds the LLM permanently, no switching at all.
    NONE = "none"
    #: vLLM sleep level 1 — weights move to host RAM and come back over PCIe.
    SLEEP_TO_RAM = "vllm_sleep_ram"
    #: vLLM sleep level 2 — weights are dropped and re-read from disk.
    SLEEP_TO_DISK = "vllm_sleep_disk"
    #: Stop and restart the vLLM process. The documented fallback if sleep
    #: turns out to be unstable; phase 0 decides whether it is needed.
    RESTART = "vllm_restart"


@dataclass(frozen=True)
class LlmCandidate:
    name: str
    quantisation: str
    weights_gb: float
    #: Weights plus KV cache and activations at the context length we use.
    min_vram_gb: float
    licence: str
    note: str


#: Ordered best first (handoff section 5).
LLM_CANDIDATES: tuple[LlmCandidate, ...] = (
    LlmCandidate(
        name="Qwen3-30B-A3B",
        quantisation="4-bit (AWQ or GPTQ)",
        weights_gb=18.0,
        min_vram_gb=22.0,
        licence="Apache-2.0",
        note="The handoff's first choice. MoE, so only ~3B parameters are active per token.",
    ),
    LlmCandidate(
        name="Qwen3-14B",
        quantisation="4-bit",
        weights_gb=9.0,
        min_vram_gb=13.0,
        licence="Apache-2.0",
        note="The handoff's fallback. Fits alongside far more headroom.",
    ),
    LlmCandidate(
        name="Gemma-3-12B",
        quantisation="4-bit",
        weights_gb=7.5,
        min_vram_gb=11.0,
        licence="Gemma Terms of Use",
        note="Not Apache-2.0 — review the Gemma terms against decision D6 before using it.",
    ),
)


@dataclass(frozen=True)
class ModelChoice:
    role: str
    model: str
    vram_gb: float
    licence: str
    reason: str


@dataclass(frozen=True)
class Finding:
    level: str  # "ok" | "warning" | "blocker"
    topic: str
    message: str
    remedy: str | None = None


@dataclass(frozen=True)
class Recommendation:
    probe: PlatformProbe
    llm: LlmCandidate | None
    models: list[ModelChoice]
    switch_strategy: SwitchStrategy
    switch_seconds: float
    #: "measured" | "estimated" — never silently mixed.
    switch_basis: str
    settings: dict[str, str]
    findings: list[Finding] = field(default_factory=list)

    @property
    def blockers(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "blocker"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]

    @property
    def is_viable(self) -> bool:
        return not self.blockers

    def env_fragment(self) -> str:
        lines = [
            "# Generated by scripts/analyze_platform.py — review before applying.",
            f"# GPU: {self.probe.gpu.name or 'none detected'}"
            f" ({self.probe.gpu.vram_total_gb} GB)"
            f" · RAM: {self.probe.memory.total_gb} GB"
            f" · disk: {self.probe.disk.free_gb} GB free ({self.probe.disk.kind})",
            f"# Switch strategy: {self.switch_strategy.value}"
            f" (~{self.switch_seconds:.0f}s, {self.switch_basis})",
            "",
        ]
        lines.extend(f"{key}={value}" for key, value in self.settings.items())
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# the recommendation
# ---------------------------------------------------------------------------
def _disk_read_gbps(probe: PlatformProbe) -> float:
    if probe.disk.write_mbps:
        # A benchmarked write rate is a conservative stand-in for reads.
        return max(0.05, probe.disk.write_mbps / 1024)
    if probe.disk.rotational is True:
        return HDD_READ_GBPS
    return NVME_READ_GBPS


def _pick_llm(vram_gb: float) -> LlmCandidate | None:
    for candidate in LLM_CANDIDATES:
        if vram_gb >= candidate.min_vram_gb:
            return candidate
    return None


def _estimate_switch_seconds(
    strategy: SwitchStrategy, llm: LlmCandidate, probe: PlatformProbe
) -> float:
    if strategy is SwitchStrategy.NONE:
        return 0.0
    if strategy is SwitchStrategy.SLEEP_TO_RAM:
        return llm.weights_gb / PCIE_EFFECTIVE_GBPS + SLEEP_WAKE_OVERHEAD_SECONDS
    load = llm.weights_gb / _disk_read_gbps(probe) * LOAD_PATH_FACTOR
    if strategy is SwitchStrategy.SLEEP_TO_DISK:
        return load + SLEEP_WAKE_OVERHEAD_SECONDS
    return load + VLLM_ENGINE_INIT_SECONDS


def recommend(
    probe: PlatformProbe,
    *,
    measured_switch_seconds: float | None = None,
    uptime_hours: float = ASSUMED_UPTIME_HOURS,
) -> Recommendation:
    """Derive a configuration from a probe.

    ``measured_switch_seconds`` comes from ``GpuWindowState`` once the system
    has actually switched a few times; it always beats the estimate.
    """
    findings: list[Finding] = []
    models: list[ModelChoice] = []
    vram_gb = probe.gpu.vram_total_gb
    ram_gb = probe.memory.total_gb

    # ---- GPU present at all? ------------------------------------------
    if not probe.gpu.present:
        findings.append(
            Finding(
                "blocker",
                "gpu",
                f"No NVIDIA GPU detected ({probe.gpu.error or 'unknown reason'}).",
                "Install the driver and the NVIDIA container toolkit, then re-run. "
                "Until then the platform can only run with GPU_RUNTIME=simulated.",
            )
        )
        return Recommendation(
            probe=probe,
            llm=None,
            models=models,
            switch_strategy=SwitchStrategy.RESTART,
            switch_seconds=0.0,
            switch_basis="estimated",
            settings={"GPU_RUNTIME": "simulated"},
            findings=findings,
        )

    # ---- language model ------------------------------------------------
    llm = _pick_llm(vram_gb)
    if llm is None:
        findings.append(
            Finding(
                "blocker",
                "llm",
                f"{vram_gb} GB of VRAM is below what the smallest candidate "
                f"({LLM_CANDIDATES[-1].name}) needs ({LLM_CANDIDATES[-1].min_vram_gb} GB).",
                "A 24 GB card is what the design assumes (decision D9).",
            )
        )
        return Recommendation(
            probe=probe,
            llm=None,
            models=models,
            switch_strategy=SwitchStrategy.RESTART,
            switch_seconds=0.0,
            switch_basis="estimated",
            settings={"GPU_RUNTIME": "simulated"},
            findings=findings,
        )

    models.append(
        ModelChoice(
            "llm",
            f"{llm.name} {llm.quantisation}",
            llm.weights_gb,
            llm.licence,
            f"{vram_gb} GB VRAM covers its {llm.min_vram_gb} GB requirement. {llm.note}",
        )
    )
    if llm is not LLM_CANDIDATES[0]:
        findings.append(
            Finding(
                "warning",
                "llm",
                f"{vram_gb} GB VRAM is not enough for {LLM_CANDIDATES[0].name}"
                f" ({LLM_CANDIDATES[0].min_vram_gb} GB); falling back to {llm.name}.",
                "Phase 0's acceptance criterion is 7 of 10 articles publishable per "
                "language. Check the smaller model still clears it.",
            )
        )
    if llm.licence != "Apache-2.0":
        findings.append(
            Finding(
                "warning",
                "licence",
                f"{llm.name} is under {llm.licence}, not Apache-2.0.",
                "Decision D6 allows only commercially licensed models. Review the terms.",
            )
        )

    # ---- switch strategy: the RAM question ------------------------------
    ram_for_sleep = ram_gb - SERVICES_RESERVE_GB - COMFY_HOST_RESIDENT_GB
    needed_for_sleep = llm.weights_gb * SLEEP_RAM_HEADROOM

    if probe.gpu.count >= 2:
        strategy = SwitchStrategy.NONE
        findings.append(
            Finding(
                "ok",
                "gpu",
                f"{probe.gpu.count} GPUs detected — the LLM can hold one card "
                "permanently and never be swapped.",
                "This is the phase 4 arrangement; the scheduler needs no window switching at all.",
            )
        )
    elif ram_for_sleep >= needed_for_sleep:
        strategy = SwitchStrategy.SLEEP_TO_RAM
        findings.append(
            Finding(
                "ok",
                "memory",
                f"{ram_gb} GB RAM leaves {ram_for_sleep:.0f} GB after services "
                f"({SERVICES_RESERVE_GB:.0f} GB) and ComfyUI ({COMFY_HOST_RESIDENT_GB:.0f} GB), "
                f"enough to park {llm.name}'s {llm.weights_gb} GB of weights.",
            )
        )
    else:
        strategy = SwitchStrategy.SLEEP_TO_DISK
        smaller = next(
            (
                candidate
                for candidate in LLM_CANDIDATES
                if candidate is not llm
                and vram_gb >= candidate.min_vram_gb
                and candidate.weights_gb * SLEEP_RAM_HEADROOM <= ram_for_sleep
            ),
            None,
        )
        ram_needed = SERVICES_RESERVE_GB + COMFY_HOST_RESIDENT_GB + needed_for_sleep
        remedy = (
            f"Either add RAM — {ram_needed:.0f} GB is the minimum for sleep-to-RAM "
            f"with {llm.name}, and the handoff asks for 64 GB — or accept the "
            "slower switch."
        )
        if smaller is not None:
            remedy += (
                f" A third option: drop to {smaller.name}, whose {smaller.weights_gb} GB "
                "does fit in RAM, trading output quality for switch speed."
            )
        findings.append(
            Finding(
                "warning",
                "memory",
                f"{ram_gb} GB RAM leaves only {ram_for_sleep:.0f} GB after services and "
                f"ComfyUI, short of the {needed_for_sleep:.0f} GB needed to park "
                f"{llm.name}'s weights. Every window switch must re-read them from disk. "
                "The same shortfall also denies the page cache room to keep the weight "
                "files, so the re-read really does reach the device each time.",
                remedy,
            )
        )

    if measured_switch_seconds is not None and measured_switch_seconds > 0:
        switch_seconds = measured_switch_seconds
        basis = "measured"
    else:
        switch_seconds = _estimate_switch_seconds(strategy, llm, probe)
        basis = "estimated"

    if strategy is SwitchStrategy.SLEEP_TO_DISK and probe.disk.rotational is True:
        findings.append(
            Finding(
                "blocker",
                "disk",
                f"Weights must be re-read from disk on every switch, but "
                f"{probe.disk.device or 'the root device'} is a spinning disk — "
                f"about {switch_seconds:.0f}s per switch.",
                "Put the model weights on NVMe. This is the single most effective "
                "change available on this machine.",
            )
        )

    # ---- media models ---------------------------------------------------
    models.append(
        ModelChoice("image", "FLUX.1-schnell (fp8)", 14.0, "Apache-2.0", "Handoff section 5.")
    )
    nightly_video = vram_gb >= WAN_MIN_VRAM_GB
    if nightly_video:
        models.append(
            ModelChoice(
                "video",
                "Wan 2.2 TI2V-5B",
                WAN_MIN_VRAM_GB,
                "Apache-2.0",
                "Runs in the night window; the 14B variant needs offload and is far slower.",
            )
        )
    else:
        findings.append(
            Finding(
                "warning",
                "video",
                f"{vram_gb} GB VRAM is below the {WAN_MIN_VRAM_GB} GB Wan 2.2 TI2V-5B "
                "needs. AI video clips are unavailable.",
                "video_mode `none` and `voice` still work — they need no clip model. "
                "Set GPU_NIGHTLY_VIDEO_ENABLED=false so nothing queues work that "
                "cannot run.",
            )
        )

    whisper = "large-v3" if vram_gb >= 8 else "medium"
    models.append(
        ModelChoice(
            "transcribe",
            f"faster-whisper {whisper}",
            5.0 if whisper == "large-v3" else 2.5,
            "MIT",
            "Subtitle timing for video_mode voice and face.",
        )
    )
    lipsync = "LatentSync 1.5" if vram_gb >= 12 else "MuseTalk"
    models.append(
        ModelChoice("lipsync", lipsync, 8.0, "Apache-2.0 / MIT", "Only used by video_mode face.")
    )
    models.append(
        ModelChoice("tts_fa", "Piper (fa_IR)", 0.0, "per voice — check each", "CPU only; no VRAM.")
    )
    models.append(
        ModelChoice("tts_en_ar", "Chatterbox Multilingual", 2.0, "MIT", "No Persian support.")
    )

    # ---- batching follows the switch cost -------------------------------
    # Stay in a window long enough that switching costs at most
    # TARGET_SWITCH_OVERHEAD of the time.
    batch_seconds = int(
        min(3600, max(600, switch_seconds * (1 - TARGET_SWITCH_OVERHEAD) / TARGET_SWITCH_OVERHEAD))
    )
    starvation_seconds = int(max(1800, batch_seconds * 2))
    batch_size = 8 if switch_seconds < 30 else 16

    overhead_fraction = switch_seconds / (switch_seconds + batch_seconds) if batch_seconds else 0
    capacity_seconds = int(uptime_hours * 3600 * (1 - overhead_fraction))

    # ---- CPU and disk ----------------------------------------------------
    cores = probe.cpu.physical_cores or probe.cpu.logical_cores
    # One core for the GPU worker, one for the OS and the database.
    cpu_concurrency = max(2, cores - 2)
    if cores < 4:
        findings.append(
            Finding(
                "warning",
                "cpu",
                f"{cores} physical cores. Persian TTS (Piper) and FFmpeg muxing both "
                "run on the CPU and will queue behind each other.",
                "8 cores or more keeps the CPU queues clear of the GPU pipeline.",
            )
        )

    weights_gb_total = sum(choice.vram_gb for choice in models) + llm.weights_gb
    if probe.disk.free_gb < weights_gb_total * 2:
        findings.append(
            Finding(
                "warning",
                "disk",
                f"{probe.disk.free_gb} GB free. The model weights alone are roughly "
                f"{weights_gb_total:.0f} GB, before any generated media.",
                "Allow for the weights plus the MinIO bucket, which grows with every "
                "package and is never pruned automatically.",
            )
        )
    else:
        findings.append(
            Finding(
                "ok",
                "disk",
                f"{probe.disk.free_gb} GB free is comfortable for roughly "
                f"{weights_gb_total:.0f} GB of weights plus generated media.",
            )
        )

    settings = {
        "GPU_RUNTIME": "simulated",
        "GPU_WINDOW_SWITCH_SECONDS": str(int(round(switch_seconds))),
        "GPU_MAX_BATCH_SECONDS": str(batch_seconds),
        "GPU_MAX_BATCH_SIZE": str(batch_size),
        "GPU_WINDOW_STARVATION_SECONDS": str(starvation_seconds),
        "GPU_DAILY_CAPACITY_SECONDS": str(capacity_seconds),
        "GPU_NIGHTLY_VIDEO_ENABLED": "true" if nightly_video else "false",
        "CELERY_CPU_CONCURRENCY": str(cpu_concurrency),
        "DB_POOL_SIZE": str(min(20, max(5, cores))),
        "DB_MAX_OVERFLOW": str(min(30, max(10, cores * 2))),
    }

    return Recommendation(
        probe=probe,
        llm=llm,
        models=models,
        switch_strategy=strategy,
        switch_seconds=switch_seconds,
        switch_basis=basis,
        settings=settings,
        findings=findings,
    )
