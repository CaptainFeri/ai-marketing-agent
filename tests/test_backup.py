"""Daily backups: a real ``pg_dump`` against the test database, and a real
mirror of an in-memory storage backend onto disk.

No mocking of ``pg_dump`` itself — the test database
``scripts/dev_postgres.sh`` starts is a real PostgreSQL instance, so this
exercises the actual subprocess and actual gzip'd SQL, the same way
``tests/test_video_compose.py`` shells out to a real ``ffmpeg``.
"""

from __future__ import annotations

import gzip
from datetime import date
from pathlib import Path

import pytest

from app.services.backup import BackupError, run_backup
from app.services.storage import InMemoryStorageBackend, tenant_key
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def backend() -> InMemoryStorageBackend:
    store = InMemoryStorageBackend()
    store.put(tenant_key("acme", "images", "a.png"), b"\x89PNG-a")
    store.put(tenant_key("acme", "images", "b.png"), b"\x89PNG-b")
    store.put(tenant_key("globex", "docs", "c.txt"), b"hello")
    return store


def test_a_real_pg_dump_lands_gzip_compressed(
    backend: InMemoryStorageBackend, tmp_path: Path, migrated_database: None
) -> None:
    result = run_backup(backend=backend, backup_dir=str(tmp_path), day=date(2026, 1, 15))

    dump_path = Path(result.postgres_dump_path)
    assert dump_path == tmp_path / "2026-01-15" / "postgres.sql.gz"
    assert dump_path.exists()
    assert result.postgres_dump_bytes == dump_path.stat().st_size

    sql = gzip.decompress(dump_path.read_bytes()).decode("utf-8", "replace")
    assert "PostgreSQL database dump" in sql
    # A table this platform actually created, not just an empty dump.
    assert "tenant" in sql


def test_every_object_in_storage_is_mirrored_to_disk(
    backend: InMemoryStorageBackend, tmp_path: Path, migrated_database: None
) -> None:
    result = run_backup(backend=backend, backup_dir=str(tmp_path), day=date(2026, 1, 15))

    assert result.objects_backed_up == 3
    storage_dir = tmp_path / "2026-01-15" / "storage"
    assert (storage_dir / "acme" / "images" / "a.png").read_bytes() == b"\x89PNG-a"
    assert (storage_dir / "acme" / "images" / "b.png").read_bytes() == b"\x89PNG-b"
    assert (storage_dir / "globex" / "docs" / "c.txt").read_bytes() == b"hello"
    assert result.objects_bytes == sum(len(v) for v in (b"\x89PNG-a", b"\x89PNG-b", b"hello"))


def test_an_empty_storage_backend_backs_up_zero_objects(
    tmp_path: Path, migrated_database: None
) -> None:
    result = run_backup(
        backend=InMemoryStorageBackend(), backup_dir=str(tmp_path), day=date(2026, 1, 15)
    )
    assert result.objects_backed_up == 0
    assert (tmp_path / "2026-01-15" / "storage").exists()


def test_backups_older_than_the_retention_window_are_pruned(
    backend: InMemoryStorageBackend, tmp_path: Path, migrated_database: None
) -> None:
    old_dir = tmp_path / "2020-01-01"
    old_dir.mkdir(parents=True)
    (old_dir / "postgres.sql.gz").write_bytes(b"stale")
    recent_dir = tmp_path / "2025-12-31"
    recent_dir.mkdir(parents=True)
    (recent_dir / "postgres.sql.gz").write_bytes(b"recent")

    result = run_backup(
        backend=backend, backup_dir=str(tmp_path), day=date(2026, 1, 1), retention_days=7
    )

    assert result.pruned_days == 1
    assert not old_dir.exists()
    assert recent_dir.exists()
    assert (tmp_path / "2026-01-01").exists()


def test_a_non_dated_directory_under_backup_dir_is_left_alone(
    backend: InMemoryStorageBackend, tmp_path: Path, migrated_database: None
) -> None:
    stray = tmp_path / "not-a-date"
    stray.mkdir()
    run_backup(backend=backend, backup_dir=str(tmp_path), day=date(2026, 1, 1), retention_days=1)
    assert stray.exists()


def test_a_missing_pg_dump_binary_is_reported_clearly(
    backend: InMemoryStorageBackend, tmp_path: Path, migrated_database: None
) -> None:
    with pytest.raises(BackupError, match="not on PATH"):
        run_backup(
            backend=backend,
            backup_dir=str(tmp_path),
            pg_dump_binary="definitely-not-a-real-binary",
        )


# ---------------------------------------------------------------------------
# the Celery task
# ---------------------------------------------------------------------------
def test_the_backup_task_reports_a_summary(tmp_path: Path, system_db) -> None:
    from app.core.config import settings
    from app.services.storage import set_backend
    from app.worker.tasks.maintenance import run_daily_backup

    set_backend(InMemoryStorageBackend())
    original_dir = settings.backup_dir
    settings.backup_dir = str(tmp_path)
    try:
        result = run_daily_backup()
    finally:
        settings.backup_dir = original_dir
        set_backend(None)

    assert result["objects_backed_up"] == 0
    assert result["postgres_bytes"] > 0
    assert "day" in result
