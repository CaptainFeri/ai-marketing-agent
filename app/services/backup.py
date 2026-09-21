"""Daily backups: a Postgres dump and a mirror of every object in storage.

Handoff section 7, week 9: "پشتیبان‌گیری روزانه از Postgres و MinIO" — daily
backup of Postgres and MinIO. Both halves matter together — a Postgres dump
without the matching MinIO objects restores rows pointing at files that no
longer exist (the same warning ``docs/deployment.md`` gave for the manual
commands this automates).

Writes to a local directory (``BACKUP_DIR``), one subfolder per day, rather
than a second MinIO bucket: the handoff's single-server deployment has
nowhere else to put it, and a local directory is trivially rsynced, tarred
or snapshotted off-box by whatever the operator already uses for host-level
backups. ``pg_dump`` and the object mirror are both real — a deterministic
CLI tool and a storage read/write loop, neither is a model with weights
phase 0 has to validate, the same reasoning as ``app.services.video_compose``.
"""

from __future__ import annotations

import gzip
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy.engine import make_url

from app.core.config import settings
from app.services.storage import StorageBackend

logger = logging.getLogger(__name__)


class BackupError(RuntimeError):
    """The dump or the mirror could not be produced."""


@dataclass
class BackupResult:
    day: date
    postgres_dump_path: str
    postgres_dump_bytes: int
    objects_backed_up: int
    objects_bytes: int
    pruned_days: int


def _dump_postgres(destination: Path, *, pg_dump_binary: str) -> int:
    """A gzip-compressed ``pg_dump`` of the system database.

    Returns the compressed size in bytes.
    """
    if shutil.which(pg_dump_binary) is None:
        raise BackupError(f"{pg_dump_binary!r} is not on PATH")

    url = make_url(settings.effective_system_database_url)
    if not url.database:
        raise BackupError("the system database URL has no database name")

    cmd = [
        pg_dump_binary,
        "--format=plain",
        "--no-owner",
        "--no-privileges",
        "-h",
        url.host or "localhost",
        "-p",
        str(url.port or 5432),
        "-U",
        url.username or "postgres",
        "-d",
        url.database,
    ]
    env = {**os.environ, "PGPASSWORD": url.password or ""}
    try:
        result = subprocess.run(cmd, capture_output=True, check=True, env=env)  # noqa: S603
    except FileNotFoundError as exc:
        raise BackupError(f"{pg_dump_binary!r} is not installed") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (
            exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else exc.stderr
        )
        raise BackupError(f"pg_dump failed: {stderr[-2000:]}") from exc

    compressed = gzip.compress(result.stdout)
    destination.write_bytes(compressed)
    return len(compressed)


def _mirror_storage(destination_dir: Path, backend: StorageBackend) -> tuple[int, int]:
    """Every object in storage, written under ``destination_dir`` at a path
    mirroring its key. Returns ``(object count, total bytes)``."""
    destination_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    total_bytes = 0
    for key in backend.list_objects():
        data = backend.get(key)
        target = destination_dir / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        count += 1
        total_bytes += len(data)
    return count, total_bytes


def _prune_old_backups(root: Path, retention_days: int, *, reference_day: date) -> int:
    """Delete dated backup directories older than the retention window,
    measured back from ``reference_day`` — the day the backup being taken
    right now is for, not necessarily the real wall-clock date, so backing
    up a specific past day (a manual catch-up run) never prunes the backup
    it just wrote.

    Returns how many were removed.
    """
    if not root.exists():
        return 0
    cutoff = reference_day - timedelta(days=retention_days)
    pruned = 0
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            entry_day = date.fromisoformat(entry.name)
        except ValueError:
            continue
        if entry_day < cutoff:
            shutil.rmtree(entry)
            pruned += 1
    return pruned


def run_backup(
    *,
    backend: StorageBackend,
    backup_dir: str | None = None,
    retention_days: int | None = None,
    pg_dump_binary: str | None = None,
    day: date | None = None,
) -> BackupResult:
    """Dump Postgres, mirror every object in ``backend``, and prune anything
    older than the retention window — in that order, so a failed mirror
    never leaves stale backups pruned with nothing to replace them."""
    day = day or datetime.now(UTC).date()
    root = Path(backup_dir or settings.backup_dir)
    day_dir = root / day.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)

    dump_path = day_dir / "postgres.sql.gz"
    dump_bytes = _dump_postgres(dump_path, pg_dump_binary=pg_dump_binary or settings.pg_dump_binary)

    object_count, object_bytes = _mirror_storage(day_dir / "storage", backend)

    pruned = _prune_old_backups(
        root,
        retention_days if retention_days is not None else settings.backup_retention_days,
        reference_day=day,
    )

    logger.info(
        "daily backup finished",
        extra={
            "day": day.isoformat(),
            "postgres_bytes": dump_bytes,
            "objects": object_count,
            "object_bytes": object_bytes,
            "pruned_days": pruned,
        },
    )
    return BackupResult(
        day=day,
        postgres_dump_path=str(dump_path),
        postgres_dump_bytes=dump_bytes,
        objects_backed_up=object_count,
        objects_bytes=object_bytes,
        pruned_days=pruned,
    )


__all__ = ["BackupError", "BackupResult", "run_backup"]
