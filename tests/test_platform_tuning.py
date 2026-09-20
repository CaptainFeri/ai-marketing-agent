"""Hardware probing and the configuration it implies.

The probe itself has to work on whatever machine the suite runs on, so it is
tested for graceful degradation rather than for specific values. The
recommender is tested against synthetic probes, including the one that matches
the actual target server.
"""

from __future__ import annotations

import pytest

from app.core.platform import (
    CpuProbe,
    DiskProbe,
    GpuProbe,
    MemoryProbe,
    PlatformProbe,
    measure_write_mbps,
    probe_cpu,
    probe_disk,
    probe_gpu,
    probe_memory,
    probe_platform,
)
from app.services.tuning import (
    LLM_CANDIDATES,
    SwitchStrategy,
    recommend,
)


def make_probe(
    *,
    vram_gb: float = 24.0,
    ram_gb: float = 64.0,
    gpu_count: int = 1,
    rotational: bool = False,
    free_gb: float = 1900.0,
    cores: int = 16,
    gpu_present: bool = True,
) -> PlatformProbe:
    return PlatformProbe(
        gpu=GpuProbe(
            present=gpu_present,
            name="NVIDIA GeForce RTX 3090 Ti" if gpu_present else None,
            count=gpu_count if gpu_present else 0,
            vram_total_mb=int(vram_gb * 1024) if gpu_present else None,
            vram_free_mb=int(vram_gb * 1024) if gpu_present else None,
            driver_version="550.54.14" if gpu_present else None,
            error=None if gpu_present else "nvidia-smi not found",
        ),
        memory=MemoryProbe(
            total_mb=int(ram_gb * 1024),
            available_mb=int(ram_gb * 1024 * 0.96),
            swap_total_mb=8192,
        ),
        disk=DiskProbe(
            path="/",
            total_gb=2048.0,
            free_gb=free_gb,
            rotational=rotational,
            device="nvme0n1",
        ),
        cpu=CpuProbe(model="AMD Ryzen 9 5950X", physical_cores=cores, logical_cores=cores * 2),
        system="Linux",
        kernel="6.8.0",
        probed_at="2026-09-20T08:00:00Z",
    )


# ---------------------------------------------------------------------------
# probing
# ---------------------------------------------------------------------------
def test_probing_never_raises_on_this_machine() -> None:
    """It has to run on a laptop with no GPU as well as on the server."""
    probe = probe_platform("/")
    assert probe.probed_at
    assert probe.memory.total_mb > 0
    assert probe.cpu.logical_cores >= 1


def test_a_missing_gpu_is_reported_not_raised() -> None:
    result = probe_gpu()
    if not result.present:
        assert result.error


def test_memory_reports_available_not_just_free() -> None:
    """MemFree understates badly on a machine with a warm page cache."""
    memory = probe_memory()
    assert memory.available_mb <= memory.total_mb
    assert memory.available_mb > 0


def test_disk_probe_finds_the_backing_device() -> None:
    disk = probe_disk("/")
    assert disk.total_gb > 0
    assert disk.free_gb >= 0
    assert disk.kind in {"hdd", "ssd/nvme", "unknown"}


def test_cpu_counts_cores_not_only_threads() -> None:
    cpu = probe_cpu()
    assert cpu.logical_cores >= 1
    if cpu.physical_cores:
        assert cpu.physical_cores <= cpu.logical_cores


def test_the_disk_benchmark_is_opt_in() -> None:
    assert probe_disk("/").write_mbps is None


def test_benchmarking_cleans_up_after_itself(tmp_path) -> None:
    result = measure_write_mbps(str(tmp_path), size_mb=4)
    assert result is None or result > 0
    assert list(tmp_path.iterdir()) == []


def test_an_unwritable_path_does_not_raise() -> None:
    assert measure_write_mbps("/proc/nonexistent-directory", size_mb=1) is None


# ---------------------------------------------------------------------------
# model choice
# ---------------------------------------------------------------------------
def test_a_3090ti_gets_the_handoffs_first_choice() -> None:
    result = recommend(make_probe(vram_gb=24.0))
    assert result.llm is LLM_CANDIDATES[0]
    assert result.llm.name == "Qwen3-30B-A3B"
    assert result.is_viable


def test_a_smaller_card_falls_back_and_says_so() -> None:
    result = recommend(make_probe(vram_gb=16.0))
    assert result.llm.name == "Qwen3-14B"
    assert any(finding.topic == "llm" and finding.level == "warning" for finding in result.findings)


def test_a_card_too_small_for_any_candidate_is_a_blocker() -> None:
    result = recommend(make_probe(vram_gb=8.0))
    assert not result.is_viable
    assert result.llm is None


def test_no_gpu_is_a_blocker_that_still_leaves_the_platform_runnable() -> None:
    result = recommend(make_probe(gpu_present=False))
    assert not result.is_viable
    # The simulated runtime is the documented way to work without models.
    assert result.settings["GPU_RUNTIME"] == "simulated"


def test_a_non_apache_model_raises_a_licence_finding() -> None:
    """Decision D6 allows only commercially licensed models."""
    result = recommend(make_probe(vram_gb=11.5))
    assert result.llm.licence != "Apache-2.0"
    assert any(finding.topic == "licence" for finding in result.findings)


# ---------------------------------------------------------------------------
# the RAM question — what decides the switch strategy
# ---------------------------------------------------------------------------
def test_64gb_ram_allows_the_weights_to_be_parked_in_memory() -> None:
    result = recommend(make_probe(ram_gb=64.0))
    assert result.switch_strategy is SwitchStrategy.SLEEP_TO_RAM
    assert not result.warnings


def test_32gb_ram_forces_a_disk_backed_switch() -> None:
    """The target server's marginal case — 32 GB does not fit the weights."""
    result = recommend(make_probe(ram_gb=32.0))
    assert result.switch_strategy is SwitchStrategy.SLEEP_TO_DISK
    memory_findings = [f for f in result.findings if f.topic == "memory"]
    assert memory_findings and memory_findings[0].level == "warning"
    # The remedy has to name the smaller-model option, not only "buy RAM".
    assert "Qwen3-14B" in (memory_findings[0].remedy or "")


def test_a_disk_backed_switch_costs_more_than_a_ram_backed_one() -> None:
    on_disk = recommend(make_probe(ram_gb=32.0))
    in_ram = recommend(make_probe(ram_gb=64.0))
    assert on_disk.switch_seconds > in_ram.switch_seconds


def test_a_spinning_disk_with_too_little_ram_is_a_blocker() -> None:
    """Re-reading 18 GB of weights from a platter on every switch is fatal."""
    result = recommend(make_probe(ram_gb=32.0, rotational=True))
    assert not result.is_viable
    assert any(finding.topic == "disk" for finding in result.blockers)


def test_a_spinning_disk_is_fine_when_the_weights_stay_in_ram() -> None:
    result = recommend(make_probe(ram_gb=64.0, rotational=True))
    assert result.switch_strategy is SwitchStrategy.SLEEP_TO_RAM
    assert result.is_viable


def test_two_gpus_remove_switching_altogether() -> None:
    """The phase 4 arrangement: one card holds the LLM permanently."""
    result = recommend(make_probe(gpu_count=2))
    assert result.switch_strategy is SwitchStrategy.NONE
    assert result.switch_seconds == 0.0


# ---------------------------------------------------------------------------
# derived settings
# ---------------------------------------------------------------------------
def test_an_expensive_switch_produces_longer_batches() -> None:
    """Batching exists to amortise the switch, so it has to scale with it."""
    cheap = recommend(make_probe(ram_gb=64.0))
    costly = recommend(make_probe(ram_gb=32.0))
    assert int(costly.settings["GPU_MAX_BATCH_SECONDS"]) >= int(
        cheap.settings["GPU_MAX_BATCH_SECONDS"]
    )


def test_capacity_is_discounted_for_switch_overhead() -> None:
    result = recommend(make_probe(), uptime_hours=20.0)
    capacity = int(result.settings["GPU_DAILY_CAPACITY_SECONDS"])
    assert capacity < 20 * 3600
    assert capacity > 20 * 3600 * 0.9


def test_a_measured_switch_time_beats_the_estimate() -> None:
    estimated = recommend(make_probe())
    measured = recommend(make_probe(), measured_switch_seconds=140.0)
    assert estimated.switch_basis == "estimated"
    assert measured.switch_basis == "measured"
    assert measured.switch_seconds == 140.0
    # And a switch that turns out to be slow means much longer batches.
    assert int(measured.settings["GPU_MAX_BATCH_SECONDS"]) > int(
        estimated.settings["GPU_MAX_BATCH_SECONDS"]
    )


def test_ai_video_is_disabled_on_a_card_that_cannot_run_wan() -> None:
    result = recommend(make_probe(vram_gb=16.0))
    assert result.settings["GPU_NIGHTLY_VIDEO_ENABLED"] == "false"
    assert any(finding.topic == "video" for finding in result.findings)


def test_ai_video_is_enabled_on_a_24gb_card() -> None:
    assert recommend(make_probe()).settings["GPU_NIGHTLY_VIDEO_ENABLED"] == "true"


def test_cpu_concurrency_leaves_room_for_the_gpu_worker() -> None:
    result = recommend(make_probe(cores=16))
    assert int(result.settings["CELERY_CPU_CONCURRENCY"]) == 14


def test_a_small_cpu_is_flagged_because_persian_tts_runs_on_it() -> None:
    result = recommend(make_probe(cores=2))
    assert any(finding.topic == "cpu" for finding in result.findings)
    assert int(result.settings["CELERY_CPU_CONCURRENCY"]) >= 2


def test_a_nearly_full_disk_is_flagged() -> None:
    result = recommend(make_probe(free_gb=40.0))
    disk_findings = [f for f in result.findings if f.topic == "disk"]
    assert disk_findings and disk_findings[0].level == "warning"


def test_two_terabytes_is_reported_as_comfortable() -> None:
    result = recommend(make_probe(free_gb=1900.0))
    disk_findings = [f for f in result.findings if f.topic == "disk"]
    assert disk_findings and disk_findings[0].level == "ok"


# ---------------------------------------------------------------------------
# the env fragment
# ---------------------------------------------------------------------------
def test_every_emitted_key_is_a_real_setting() -> None:
    """A typo here would be silently ignored by pydantic-settings."""
    from app.core.config import Settings

    known = {name.upper() for name in Settings.model_fields}
    for key in recommend(make_probe()).settings:
        assert key in known, f"{key} is not a Settings field"


def test_the_env_fragment_is_parseable_and_self_describing() -> None:
    result = recommend(make_probe(ram_gb=32.0))
    fragment = result.env_fragment()
    assert fragment.startswith("#")
    assert "vllm_sleep_disk" in fragment

    parsed = dict(
        line.split("=", 1) for line in fragment.splitlines() if line and not line.startswith("#")
    )
    assert parsed == result.settings


@pytest.mark.parametrize("ram_gb", [16.0, 32.0, 48.0, 64.0, 128.0])
def test_a_recommendation_is_produced_at_any_memory_size(ram_gb: float) -> None:
    result = recommend(make_probe(ram_gb=ram_gb))
    assert result.settings
    assert result.findings


def test_the_analysis_endpoint_is_operator_only(monkeypatch) -> None:
    """Host hardware is nobody's business but the operator's."""
    import uuid

    from app.api.deps import Principal
    from app.api.v1.platform import analyse
    from app.core.errors import PermissionDeniedError

    tenant_user = Principal(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        email="owner@example.com",
        is_superuser=False,
        memberships=(),
    )
    # Called directly rather than through the client, so the Query defaults
    # have to be supplied by hand.
    with pytest.raises(PermissionDeniedError):
        analyse(disk="/", benchmark=False, principal=tenant_user)

    operator = Principal(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        email="ops@example.com",
        is_superuser=True,
        memberships=(),
    )
    result = analyse(disk="/", benchmark=False, principal=operator)
    assert "probe" in result
    assert result["switch_basis"] in {"measured", "estimated"}
    assert "env_fragment" in result
