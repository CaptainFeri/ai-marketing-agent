"""Hardware probing.

Reads what the server actually has — GPU, RAM, disk, CPU — so the platform can
choose its own configuration instead of asking an operator to guess.  Used by
``app.services.tuning`` and by ``scripts/analyze_platform.py``.

Everything degrades gracefully: a missing ``nvidia-smi``, a non-Linux host or
an unreadable ``/proc`` file produces ``None`` and a finding, never a crash.
The analyzer has to run on a laptop during development as well as on the real
server.
"""

from __future__ import annotations

import contextlib
import os
import platform
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

MB = 1024 * 1024
GB = 1024 * MB


@dataclass(frozen=True)
class GpuProbe:
    present: bool = False
    name: str | None = None
    count: int = 0
    vram_total_mb: int | None = None
    vram_free_mb: int | None = None
    driver_version: str | None = None
    compute_capability: str | None = None
    error: str | None = None

    @property
    def vram_total_gb(self) -> float:
        return round((self.vram_total_mb or 0) / 1024, 1)


@dataclass(frozen=True)
class MemoryProbe:
    total_mb: int = 0
    available_mb: int = 0
    swap_total_mb: int = 0

    @property
    def total_gb(self) -> float:
        return round(self.total_mb / 1024, 1)

    @property
    def available_gb(self) -> float:
        return round(self.available_mb / 1024, 1)


@dataclass(frozen=True)
class DiskProbe:
    path: str = "/"
    total_gb: float = 0.0
    free_gb: float = 0.0
    #: True = spinning, False = solid state, None = could not tell.
    rotational: bool | None = None
    device: str | None = None
    #: Only set when the probe was asked to benchmark; writes a temp file.
    write_mbps: float | None = None

    @property
    def kind(self) -> str:
        if self.rotational is None:
            return "unknown"
        return "hdd" if self.rotational else "ssd/nvme"


@dataclass(frozen=True)
class CpuProbe:
    model: str | None = None
    physical_cores: int | None = None
    logical_cores: int = 1


@dataclass(frozen=True)
class PlatformProbe:
    gpu: GpuProbe = field(default_factory=GpuProbe)
    memory: MemoryProbe = field(default_factory=MemoryProbe)
    disk: DiskProbe = field(default_factory=DiskProbe)
    cpu: CpuProbe = field(default_factory=CpuProbe)
    system: str = ""
    kernel: str = ""
    probed_at: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# GPU
# ---------------------------------------------------------------------------
_NVIDIA_FIELDS = "name,memory.total,memory.free,driver_version,compute_cap"


def probe_gpu(timeout: float = 10.0) -> GpuProbe:
    if shutil.which("nvidia-smi") is None:
        return GpuProbe(error="nvidia-smi not found")
    # compute_cap is missing on older drivers; fall back rather than give up.
    for fields in (_NVIDIA_FIELDS, "name,memory.total,memory.free,driver_version"):
        try:
            output = subprocess.run(
                ["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=True,
            ).stdout.strip()
        except (subprocess.SubprocessError, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            continue

        lines = [line for line in output.splitlines() if line.strip()]
        if not lines:
            return GpuProbe(error="nvidia-smi reported no devices")

        parts = [part.strip() for part in lines[0].split(",")]
        try:
            return GpuProbe(
                present=True,
                name=parts[0],
                count=len(lines),
                vram_total_mb=int(float(parts[1])),
                vram_free_mb=int(float(parts[2])),
                driver_version=parts[3] if len(parts) > 3 else None,
                compute_capability=parts[4] if len(parts) > 4 else None,
            )
        except (IndexError, ValueError) as exc:
            last_error = f"could not parse nvidia-smi output: {exc}"
            continue

    return GpuProbe(error=last_error)


# ---------------------------------------------------------------------------
# memory
# ---------------------------------------------------------------------------
def probe_memory() -> MemoryProbe:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        # Non-Linux fallback; enough to keep the analyzer running locally.
        try:
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // MB
        except (ValueError, OSError, AttributeError):
            return MemoryProbe()
        return MemoryProbe(total_mb=total, available_mb=total)

    values: dict[str, int] = {}
    for line in meminfo.read_text().splitlines():
        match = re.match(r"^(\w+):\s+(\d+)\s*kB", line)
        if match:
            values[match.group(1)] = int(match.group(2)) // 1024

    return MemoryProbe(
        total_mb=values.get("MemTotal", 0),
        # MemAvailable accounts for reclaimable cache; MemFree understates badly.
        available_mb=values.get("MemAvailable", values.get("MemFree", 0)),
        swap_total_mb=values.get("SwapTotal", 0),
    )


# ---------------------------------------------------------------------------
# disk
# ---------------------------------------------------------------------------
def _block_device_for(path: str) -> str | None:
    """Resolve the backing block device, following partitions to their parent."""
    try:
        stat = os.stat(path)
        major, minor = os.major(stat.st_dev), os.minor(stat.st_dev)
        link = Path(f"/sys/dev/block/{major}:{minor}")
        if not link.exists():
            return None
        resolved = link.resolve()
        # A partition (e.g. nvme0n1p2) has its own `partition` file; the
        # rotational flag lives on the parent device.
        if (resolved / "partition").exists():
            resolved = resolved.parent
        return resolved.name
    except OSError:
        return None


def probe_disk(path: str = "/", benchmark_mb: int = 0) -> DiskProbe:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return DiskProbe(path=path)

    device = _block_device_for(path)
    rotational: bool | None = None
    if device:
        flag = Path(f"/sys/block/{device}/queue/rotational")
        if flag.exists():
            try:
                rotational = flag.read_text().strip() == "1"
            except OSError:
                rotational = None

    return DiskProbe(
        path=path,
        total_gb=round(usage.total / GB, 1),
        free_gb=round(usage.free / GB, 1),
        rotational=rotational,
        device=device,
        write_mbps=measure_write_mbps(path, benchmark_mb) if benchmark_mb else None,
    )


def measure_write_mbps(path: str, size_mb: int = 256) -> float | None:
    """Time a sequential write with fsync.

    A lower bound on the device's throughput, which is what matters for model
    loading: a window switch that reloads weights from disk is bounded by this
    number. Reads are usually faster, so treating this as the read speed is
    conservative in the right direction.

    Writes and then deletes ``size_mb`` of data, so it is opt-in.
    """
    target = Path(path) / f".ai-marketing-diskbench-{os.getpid()}"
    chunk = b"\0" * MB
    try:
        start = time.monotonic()
        with open(target, "wb") as handle:
            for _ in range(size_mb):
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        elapsed = time.monotonic() - start
    except OSError:
        return None
    finally:
        with contextlib.suppress(OSError):
            target.unlink(missing_ok=True)

    if elapsed <= 0:
        return None
    return round(size_mb / elapsed, 1)


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------
def probe_cpu() -> CpuProbe:
    logical = os.cpu_count() or 1
    cpuinfo = Path("/proc/cpuinfo")
    if not cpuinfo.exists():
        return CpuProbe(logical_cores=logical)

    model: str | None = None
    # (physical id, core id) pairs; counting them gives real cores, not threads.
    cores: set[tuple[str, str]] = set()
    current: dict[str, str] = {}
    for line in cpuinfo.read_text().splitlines():
        if not line.strip():
            if "physical id" in current and "core id" in current:
                cores.add((current["physical id"], current["core id"]))
            current = {}
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        current[key] = value
        if key == "model name" and model is None:
            model = value
    if "physical id" in current and "core id" in current:
        cores.add((current["physical id"], current["core id"]))

    return CpuProbe(
        model=model,
        physical_cores=len(cores) or None,
        logical_cores=logical,
    )


# ---------------------------------------------------------------------------
# everything at once
# ---------------------------------------------------------------------------
def probe_platform(disk_path: str = "/", benchmark_mb: int = 0) -> PlatformProbe:
    return PlatformProbe(
        gpu=probe_gpu(),
        memory=probe_memory(),
        disk=probe_disk(disk_path, benchmark_mb),
        cpu=probe_cpu(),
        system=platform.system(),
        kernel=platform.release(),
        probed_at=datetime.now(UTC).isoformat(),
    )
