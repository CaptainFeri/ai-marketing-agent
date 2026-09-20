"""Decision D8: one customer's data must be unreachable from another's session.

The handoff makes this an acceptance criterion for phase 1 ("an automated
isolation test"), so these checks exercise the real PostgreSQL policies rather
than application-level filtering — application filtering is what row level
security exists to survive the absence of.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.db.models import TENANT_SCOPED_TABLES, ContentPackage, Workspace
from app.db.tenancy import bind_tenant
from tests.conftest import requires_db

pytestmark = requires_db


def _package(tenant_id, workspace_id, title: str) -> ContentPackage:
    return ContentPackage(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        title=title,
        locale="fa",
    )


def test_policy_list_matches_models() -> None:
    """The migration's policy list and the model list must not drift apart.

    Without this, adding a tenant table and forgetting its policy would be a
    silent cross-tenant leak.
    """
    import importlib.util

    path = Path("migrations/versions/0002_row_level_security.py")
    spec = importlib.util.spec_from_file_location("rls_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert set(module.TENANT_TABLES) == set(TENANT_SCOPED_TABLES)


def test_every_tenant_table_has_a_policy(system_db) -> None:
    rows = system_db.execute(
        text(
            "SELECT tablename FROM pg_policies "
            "WHERE schemaname = 'public' AND policyname = 'tenant_isolation'"
        )
    ).scalars()
    protected = set(rows)
    missing = set(TENANT_SCOPED_TABLES) - protected
    assert not missing, f"tables without a tenant_isolation policy: {sorted(missing)}"
    # ``tenant`` keys on ``id`` but is protected by the same policy name.
    assert "tenant" in protected


def test_reads_are_scoped_to_the_bound_tenant(tenant_factory, app_sessionmaker) -> None:
    alpha, alpha_ws = tenant_factory("alpha")
    beta, beta_ws = tenant_factory("beta")

    with app_sessionmaker() as session:
        bind_tenant(session, alpha.id)
        session.add(_package(alpha.id, alpha_ws.id, "alpha article"))
        session.commit()

    with app_sessionmaker() as session:
        bind_tenant(session, beta.id)
        session.add(_package(beta.id, beta_ws.id, "beta article"))
        session.commit()

    with app_sessionmaker() as session:
        bind_tenant(session, alpha.id)
        titles = session.scalars(select(ContentPackage.title)).all()
        assert titles == ["alpha article"]

    with app_sessionmaker() as session:
        bind_tenant(session, beta.id)
        titles = session.scalars(select(ContentPackage.title)).all()
        assert titles == ["beta article"]


def test_unscoped_session_sees_nothing(tenant_factory, app_sessionmaker) -> None:
    """Forgetting to bind a tenant must fail closed, not open."""
    alpha, alpha_ws = tenant_factory("alpha")
    with app_sessionmaker() as session:
        bind_tenant(session, alpha.id)
        session.add(_package(alpha.id, alpha_ws.id, "alpha article"))
        session.commit()

    with app_sessionmaker() as session:
        assert session.scalars(select(ContentPackage)).all() == []
        assert session.scalars(select(Workspace)).all() == []


def test_cannot_write_a_row_belonging_to_another_tenant(tenant_factory, app_sessionmaker) -> None:
    alpha, _ = tenant_factory("alpha")
    beta, beta_ws = tenant_factory("beta")

    with app_sessionmaker() as session:
        bind_tenant(session, alpha.id)
        session.add(_package(beta.id, beta_ws.id, "smuggled"))
        with pytest.raises(Exception) as excinfo:
            session.commit()
        assert "row-level security" in str(excinfo.value).lower()


def test_cannot_update_another_tenants_row(tenant_factory, app_sessionmaker) -> None:
    alpha, _ = tenant_factory("alpha")
    beta, beta_ws = tenant_factory("beta")

    with app_sessionmaker() as session:
        bind_tenant(session, beta.id)
        package = _package(beta.id, beta_ws.id, "beta article")
        session.add(package)
        session.commit()
        package_id = package.id

    with app_sessionmaker() as session:
        bind_tenant(session, alpha.id)
        # The UPDATE matches no visible row, so it silently affects nothing —
        # which is the correct outcome: alpha cannot even learn it exists.
        affected = session.execute(
            text("UPDATE content_package SET title = 'hijacked' WHERE id = :id"),
            {"id": package_id},
        ).rowcount
        session.commit()
        assert affected == 0

    with app_sessionmaker() as session:
        bind_tenant(session, beta.id)
        assert session.get(ContentPackage, package_id).title == "beta article"


def test_tenant_row_itself_is_scoped(tenant_factory, app_sessionmaker) -> None:
    alpha, _ = tenant_factory("alpha")
    tenant_factory("beta")

    with app_sessionmaker() as session:
        bind_tenant(session, alpha.id)
        slugs = session.execute(text("SELECT slug FROM tenant")).scalars().all()
        assert slugs == ["alpha"]


def test_binding_is_released_with_the_transaction(tenant_factory, app_sessionmaker) -> None:
    """A pooled connection must not carry one tenant's scope into the next use."""
    alpha, alpha_ws = tenant_factory("alpha")
    with app_sessionmaker() as session:
        bind_tenant(session, alpha.id)
        session.add(_package(alpha.id, alpha_ws.id, "alpha article"))
        session.commit()
        # New transaction on the same connection, no bind this time.
        assert session.scalars(select(ContentPackage)).all() == []


def test_unknown_tenant_id_sees_nothing(tenant_factory, app_sessionmaker) -> None:
    alpha, alpha_ws = tenant_factory("alpha")
    with app_sessionmaker() as session:
        bind_tenant(session, alpha.id)
        session.add(_package(alpha.id, alpha_ws.id, "alpha article"))
        session.commit()

    with app_sessionmaker() as session:
        bind_tenant(session, uuid.uuid4())
        assert session.scalars(select(ContentPackage)).all() == []
