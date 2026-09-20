"""Row level security on every tenant-scoped table (decision D8).

Each table gets one policy comparing ``tenant_id`` against the
``app.tenant_id`` GUC that ``app.db.tenancy.bind_tenant`` sets per
transaction.  ``FORCE ROW LEVEL SECURITY`` is used so the policy also applies
to the table owner — without it, the role that runs migrations would silently
see everything.

Roles that legitimately cross tenants (the GPU scheduler, the quota allocator)
connect with a separate ``BYPASSRLS`` role instead of being granted an
exception here; see ``docs/deployment.md``.

Revision ID: 0002_rls
Revises: 0001_initial
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_rls"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept in sync with ``app.db.models.TENANT_SCOPED_TABLES``;
# ``tests/test_tenant_isolation.py`` fails if the two drift apart.
TENANT_TABLES: tuple[str, ...] = (
    "membership",
    "workspace",
    "brand_brief",
    # brief_draft is added in 0005 with its own policy; it appears in
    # TENANT_SCOPED_TABLES, so the drift test looks for it here too.
    "brief_draft",
    "topic",
    "content_package",
    "step_run",
    "variant",
    "approval",
    "media_asset",
    "speaker_profile",
    "publication",
    "metric_snapshot",
    "channel_credential",
    "gpu_job",
    "gpu_quota_ledger",
)

POLICY_NAME = "tenant_isolation"


def upgrade() -> None:
    # ``tenant`` keys on ``id`` rather than ``tenant_id``, so it gets its own
    # policy.  Login has to look a user up before any tenant is known, which is
    # why ``app.services.auth`` resolves memberships over a system session.
    op.execute("ALTER TABLE tenant ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {POLICY_NAME} ON tenant
            USING (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )

    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        # ``current_setting(..., true)`` returns NULL when the GUC is unset,
        # which makes the predicate NULL and the table appear empty.  That is
        # the intended fail-closed behaviour for an unscoped connection.
        op.execute(
            f"""
            CREATE POLICY {POLICY_NAME} ON {table}
                USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            """
        )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON tenant")
    op.execute("ALTER TABLE tenant NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant DISABLE ROW LEVEL SECURITY")

    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
