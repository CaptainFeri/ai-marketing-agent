#!/usr/bin/env python
"""Look at this server and say how the platform should be configured.

    .venv/bin/python scripts/analyze_platform.py
    .venv/bin/python scripts/analyze_platform.py --benchmark --write .env.tuned

Prints what was found, the model choices that follow from it, and the
configuration those imply. Nothing is applied: the ``.env`` fragment is for a
person to read first, because getting the model choice wrong is expensive and
would otherwise be silent.

With a reachable database it also picks up the switch time the GPU worker has
actually measured, which beats anything derived from the spec sheet.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.platform import probe_platform  # noqa: E402
from app.services.tuning import Recommendation, recommend  # noqa: E402

LEVEL_MARK = {"ok": "  ok", "warning": "warn", "blocker": "STOP"}


def measured_switch_seconds() -> float | None:
    """Ask the database what a switch really costs, if it is reachable."""
    try:
        from app.db.tenancy import system_session
        from app.worker.dispatcher import measured_switch_seconds as measured

        with system_session() as session:
            return measured(session)
    except Exception:
        # The analyzer has to work before the database exists — that is when
        # it is most useful.
        return None


def render(rec: Recommendation) -> str:
    probe = rec.probe
    out: list[str] = []
    add = out.append

    add("=" * 74)
    add("  PLATFORM ANALYSIS")
    add("=" * 74)
    add("")
    add("Hardware")
    add("-" * 74)
    if probe.gpu.present:
        add(f"  GPU        {probe.gpu.name} x{probe.gpu.count}")
        add(
            f"             {probe.gpu.vram_total_gb} GB VRAM"
            f"  ·  driver {probe.gpu.driver_version or 'unknown'}"
            + (f"  ·  sm_{probe.gpu.compute_capability}" if probe.gpu.compute_capability else "")
        )
    else:
        add(f"  GPU        none detected ({probe.gpu.error})")
    add(f"  RAM        {probe.memory.total_gb} GB total, {probe.memory.available_gb} GB available")
    disk = probe.disk
    speed = f"  ·  {disk.write_mbps} MB/s write" if disk.write_mbps else ""
    add(
        f"  Disk       {disk.free_gb} GB free of {disk.total_gb} GB"
        f"  ·  {disk.kind}{speed}  ({disk.device or disk.path})"
    )
    add(
        f"  CPU        {probe.cpu.model or 'unknown'}"
        f"  ·  {probe.cpu.physical_cores or '?'} cores / {probe.cpu.logical_cores} threads"
    )
    add(f"  OS         {probe.system} {probe.kernel}")
    add("")

    if rec.models:
        add("Models")
        add("-" * 74)
        for choice in rec.models:
            vram = f"{choice.vram_gb:.0f} GB" if choice.vram_gb else "CPU"
            add(f"  {choice.role:<11}{choice.model}")
            add(f"             {vram:<9}{choice.licence}")
            add(f"             {choice.reason}")
        add("")

    add("GPU window switching")
    add("-" * 74)
    add(f"  Strategy   {rec.switch_strategy.value}")
    add(f"  Cost       ~{rec.switch_seconds:.0f}s per switch ({rec.switch_basis})")
    if rec.switch_basis == "estimated":
        add("             Phase 0 must measure this; the system then learns it")
        add("             from operation and re-batches against the real number.")
    add("")

    add("Findings")
    add("-" * 74)
    for finding in sorted(
        rec.findings, key=lambda f: {"blocker": 0, "warning": 1, "ok": 2}[f.level]
    ):
        add(f"  [{LEVEL_MARK[finding.level]}] {finding.topic}: {finding.message}")
        if finding.remedy:
            add(f"         → {finding.remedy}")
    add("")

    add("Configuration")
    add("-" * 74)
    for key, value in rec.settings.items():
        add(f"  {key}={value}")
    add("")

    if rec.blockers:
        add(f"NOT READY — {len(rec.blockers)} blocker(s) above must be resolved first.")
    elif rec.warnings:
        add(f"Usable, with {len(rec.warnings)} warning(s) worth reading before you commit.")
    else:
        add("Ready.")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--disk", default="/", help="Path whose filesystem holds model weights and MinIO"
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Time a 256 MB write to gauge the disk (writes then deletes a temp file)",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    parser.add_argument("--write", metavar="PATH", help="Also write the .env fragment there")
    parser.add_argument(
        "--uptime-hours",
        type=float,
        default=20.0,
        help="Expected daily GPU uptime (handoff section 7 assumes 20)",
    )
    args = parser.parse_args()

    probe = probe_platform(args.disk, benchmark_mb=256 if args.benchmark else 0)
    rec = recommend(
        probe,
        measured_switch_seconds=measured_switch_seconds(),
        uptime_hours=args.uptime_hours,
    )

    if args.json:
        print(
            json.dumps(
                {
                    "probe": probe.as_dict(),
                    "switch_strategy": rec.switch_strategy.value,
                    "switch_seconds": rec.switch_seconds,
                    "switch_basis": rec.switch_basis,
                    "llm": rec.llm.name if rec.llm else None,
                    "models": [vars(choice) for choice in rec.models],
                    "settings": rec.settings,
                    "findings": [vars(finding) for finding in rec.findings],
                    "is_viable": rec.is_viable,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(render(rec))

    if args.write:
        Path(args.write).write_text(rec.env_fragment(), encoding="utf-8")
        print(f"\nWrote {args.write} — review it, then merge into .env.")

    # Non-zero on a blocker so this can gate a deploy.
    return 1 if rec.blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
