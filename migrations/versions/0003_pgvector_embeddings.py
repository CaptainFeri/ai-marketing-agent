"""Optional pgvector store for brand and article embeddings (BGE-M3, 1024 dims).

The rest of the platform works without it; retrieval-augmented steps in the
research agent do not.  If the extension is unavailable the migration stops
with a clear message rather than quietly creating a table with a different
column type — a schema that differs between environments is worse than a
missing feature.  Set ``ALLOW_MISSING_PGVECTOR=1`` to skip it knowingly (the
local test cluster does this).

Revision ID: 0003_pgvector
Revises: 0002_rls
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_pgvector"
down_revision: str | None = "0002_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIMS = 1024  # BGE-M3


def _pgvector_available(connection: sa.Connection) -> bool:
    return bool(
        connection.execute(
            sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
        ).scalar()
    )


def upgrade() -> None:
    connection = op.get_bind()
    if not _pgvector_available(connection):
        if os.getenv("ALLOW_MISSING_PGVECTOR") == "1":
            print("pgvector not available; skipping embedding table (ALLOW_MISSING_PGVECTOR=1)")
            return
        raise RuntimeError(
            "pgvector is not installed on this PostgreSQL server. Install it (the "
            "pgvector/pgvector Docker image ships with it) or re-run with "
            "ALLOW_MISSING_PGVECTOR=1 to skip retrieval features."
        )

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "document_embedding",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # What the vector describes: 'article', 'brief', 'competitor_page', ...
        sa.Column("source_kind", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.dialects.postgresql.UUID(as_uuid=True)),
        sa.Column("locale", sa.String(length=8), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # SQLAlchemy has no built-in ``vector`` type, so the column is raw SQL.
    op.execute(
        f"ALTER TABLE document_embedding ADD COLUMN embedding vector({EMBEDDING_DIMS}) NOT NULL"
    )
    op.create_index("ix_document_embedding_tenant_id", "document_embedding", ["tenant_id"])
    op.create_index(
        "ix_document_embedding_source",
        "document_embedding",
        ["workspace_id", "source_kind", "source_id"],
    )
    op.execute(
        "CREATE INDEX ix_document_embedding_vector ON document_embedding "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute("ALTER TABLE document_embedding ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_embedding FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON document_embedding
            USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS document_embedding")
