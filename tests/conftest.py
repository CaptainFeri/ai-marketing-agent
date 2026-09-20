"""Test fixtures.

The suite runs against a real PostgreSQL instance: row level security is the
mechanism under test in ``test_tenant_isolation.py``, and no in-memory
substitute has it.  ``scripts/dev_postgres.sh`` starts a suitable cluster, and
``TEST_DATABASE_URL`` / ``TEST_SYSTEM_DATABASE_URL`` point at one.

Two database roles are needed, mirroring production:

* an ordinary role **without** ``BYPASSRLS`` for application queries;
* a ``BYPASSRLS`` role for the scheduler and for fixture setup.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+psycopg://app:app@127.0.0.1:5433/ai_marketing_test"
)
TEST_SYSTEM_DATABASE_URL = os.getenv(
    "TEST_SYSTEM_DATABASE_URL",
    "postgresql+psycopg://app_system:app_system@127.0.0.1:5433/ai_marketing_test",
)

# Settings are read at import time, so the environment has to be right before
# anything from ``app`` is imported.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL)
os.environ.setdefault("SYSTEM_DATABASE_URL", TEST_SYSTEM_DATABASE_URL)
os.environ.setdefault("ALLOW_MISSING_PGVECTOR", "1")
os.environ.setdefault("GPU_RUNTIME", "simulated")
os.environ.setdefault("GPU_SIMULATION_SPEEDUP", "100000")
os.environ.setdefault("STORAGE_BACKEND", "memory")
os.environ.setdefault("IMAGE_BACKEND", "simulated")


def _discover_playwright_chromium() -> str | None:
    """Find a pre-installed Chromium without hardcoding its version.

    The production Docker image runs ``playwright install chromium`` at
    build time, so the bundled browser always matches the installed
    ``playwright`` package there and needs no override. A dev sandbox can
    have an older or newer pre-installed build under a version-specific
    directory (``chromium-1194``, say), which the installed package will
    refuse to launch without an explicit path — so this looks for one.
    """
    if os.getenv("PLAYWRIGHT_EXECUTABLE_PATH"):
        return None  # respect an explicit override
    browsers_path = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
    if not browsers_path:
        return None
    from pathlib import Path

    for candidate in sorted(Path(browsers_path).glob("chromium*/chrome-linux/chrome")):
        if candidate.is_file():
            return str(candidate)
    return None


_discovered_chromium = _discover_playwright_chromium()
if _discovered_chromium:
    os.environ.setdefault("PLAYWRIGHT_EXECUTABLE_PATH", _discovered_chromium)


def _database_reachable() -> bool:
    try:
        engine = create_engine(TEST_SYSTEM_DATABASE_URL, connect_args={"connect_timeout": 2})
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _database_reachable(),
    reason=(
        "no test PostgreSQL reachable; start one with scripts/dev_postgres.sh "
        "or set TEST_DATABASE_URL"
    ),
)


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[None]:
    """Run the migrations once for the whole session."""
    if not _database_reachable():
        pytest.skip("no test database")
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_SYSTEM_DATABASE_URL)
    command.upgrade(config, "head")
    yield


@pytest.fixture
def system_db(migrated_database: None) -> Iterator[Session]:
    """A BYPASSRLS session, for fixture setup and scheduler-style queries."""
    engine = create_engine(TEST_SYSTEM_DATABASE_URL)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def clean_database(system_db: Session) -> Iterator[Session]:
    """Empty every table before the test runs."""
    from app.db.models import Base

    tables = ", ".join(
        table.name for table in Base.metadata.sorted_tables if table.name != "alembic_version"
    )
    system_db.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    system_db.commit()
    yield system_db


@pytest.fixture
def tenant_factory(clean_database: Session):
    """Create a tenant with a default workspace, returning both ids."""
    from app.db.models import Tenant, Workspace

    def _make(slug: str | None = None, *, weight: int = 1, locales: list[str] | None = None):
        slug = slug or f"t{uuid.uuid4().hex[:8]}"
        tenant = Tenant(
            slug=slug,
            name=slug.title(),
            quota_weight=weight,
            storage_prefix=slug,
            settings_json={},
            is_active=True,
        )
        clean_database.add(tenant)
        clean_database.flush()
        workspace = Workspace(
            tenant_id=tenant.id,
            slug="default",
            name=f"{slug} default",
            default_locale="fa",
            locales=locales or ["fa", "en"],
        )
        clean_database.add(workspace)
        clean_database.flush()
        clean_database.commit()
        return tenant, workspace

    return _make


@pytest.fixture
def app_sessionmaker(migrated_database: None):
    """Session factory on the non-BYPASSRLS role — the one policies apply to."""
    engine = create_engine(TEST_DATABASE_URL)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()
