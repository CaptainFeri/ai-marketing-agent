"""Initial schema — every table from handoff section 9, plus the GPU queue.

Row level security is applied separately in 0002 so the policy list stays
readable next to the table list.

Revision ID: 0001_initial
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), nullable=False),
        sa.Column("locale", sa.String(length=8), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_user")),
    )
    op.create_index(op.f("ix_app_user_email"), "app_user", ["email"], unique=True)
    op.create_table(
        "gpu_cost_estimate",
        sa.Column(
            "kind",
            sa.Enum(
                "LLM_TEXT",
                "EMBEDDING",
                "IMAGE_FLUX",
                "VIDEO_WAN",
                "TTS",
                "TRANSCRIBE_WHISPER",
                "LIPSYNC_LATENTSYNC",
                "LIPSYNC_SADTALKER",
                name="gpu_job_kind",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("locale", sa.String(length=8), nullable=False),
        sa.Column("ewma_seconds", sa.Float(), nullable=False),
        sa.Column("samples", sa.Integer(), nullable=False),
        sa.Column("last_seconds", sa.Float(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gpu_cost_estimate")),
        sa.UniqueConstraint("kind", "locale", name="kind_locale"),
    )
    op.create_table(
        "gpu_window_state",
        sa.Column("singleton", sa.Boolean(), nullable=False),
        sa.Column(
            "current_window",
            sa.Enum("TEXT", "MEDIA", name="gpu_window", native_enum=False, length=16),
            nullable=True,
        ),
        sa.Column(
            "current_kind",
            sa.Enum(
                "LLM_TEXT",
                "EMBEDDING",
                "IMAGE_FLUX",
                "VIDEO_WAN",
                "TTS",
                "TRANSCRIBE_WHISPER",
                "LIPSYNC_LATENTSYNC",
                "LIPSYNC_SADTALKER",
                name="gpu_job_kind",
                native_enum=False,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("switched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("switch_count_today", sa.Integer(), nullable=False),
        sa.Column("last_switch_seconds", sa.Float(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gpu_window_state")),
        sa.UniqueConstraint("singleton", name=op.f("uq_gpu_window_state_singleton")),
    )
    op.create_table(
        "tenant",
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "plan",
            sa.Enum(
                "TRIAL", "STARTER", "GROWTH", "SCALE", name="plan", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("quota_weight", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("storage_prefix", sa.String(length=64), nullable=False),
        sa.Column("settings_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant")),
        sa.UniqueConstraint("slug", name=op.f("uq_tenant_slug")),
    )
    op.create_table(
        "gpu_quota_ledger",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("allocated_seconds", sa.Float(), nullable=False),
        sa.Column("reserved_seconds", sa.Float(), nullable=False),
        sa.Column("consumed_seconds", sa.Float(), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("capacity_seconds", sa.Float(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "consumed_seconds >= 0", name=op.f("ck_gpu_quota_ledger_consumed_non_negative")
        ),
        sa.CheckConstraint(
            "reserved_seconds >= 0", name=op.f("ck_gpu_quota_ledger_reserved_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_gpu_quota_ledger_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gpu_quota_ledger")),
        sa.UniqueConstraint("tenant_id", "day", name="tenant_day"),
    )
    op.create_index(op.f("ix_gpu_quota_ledger_day"), "gpu_quota_ledger", ["day"], unique=False)
    op.create_index(
        op.f("ix_gpu_quota_ledger_tenant_id"), "gpu_quota_ledger", ["tenant_id"], unique=False
    )
    op.create_table(
        "workspace",
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("default_locale", sa.String(length=8), nullable=False),
        sa.Column("locales", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("calendar", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_workspace_tenant_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace")),
        sa.UniqueConstraint("tenant_id", "slug", name="tenant_slug"),
    )
    op.create_index(op.f("ix_workspace_tenant_id"), "workspace", ["tenant_id"], unique=False)
    op.create_table(
        "brand_brief",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "video_mode",
            sa.Enum("NONE", "VOICE", "FACE", name="video_mode", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["app_user.id"],
            name=op.f("fk_brand_brief_created_by_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_brand_brief_tenant_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_brand_brief_workspace_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brand_brief")),
        sa.UniqueConstraint("workspace_id", "version", name="workspace_version"),
    )
    op.create_index(op.f("ix_brand_brief_tenant_id"), "brand_brief", ["tenant_id"], unique=False)
    op.create_table(
        "channel_credential",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column(
            "channel",
            sa.Enum(
                "WORDPRESS",
                "TELEGRAM",
                "INSTAGRAM",
                "LINKEDIN",
                "X",
                "YOUTUBE",
                "APARAT",
                name="channel",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("encrypted_payload", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("public_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_channel_credential_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_channel_credential_workspace_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_channel_credential")),
        sa.UniqueConstraint("workspace_id", "channel", "label", name="workspace_channel_label"),
    )
    op.create_index(
        op.f("ix_channel_credential_tenant_id"), "channel_credential", ["tenant_id"], unique=False
    )
    op.create_table(
        "membership",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column(
            "role",
            sa.Enum(
                "OWNER", "ADMIN", "EDITOR", "VIEWER", name="role", native_enum=False, length=16
            ),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_membership_tenant_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name=op.f("fk_membership_user_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_membership_workspace_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_membership")),
        sa.UniqueConstraint("tenant_id", "user_id", "workspace_id", name="tenant_user_workspace"),
    )
    op.create_index(op.f("ix_membership_tenant_id"), "membership", ["tenant_id"], unique=False)
    op.create_table(
        "speaker_profile",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "FACE", "VOICE", "FACE_AND_VOICE", name="speaker_kind", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("assets", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("consent_document_key", sa.String(length=1024), nullable=True),
        sa.Column("consent_signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_expires_on", sa.Date(), nullable=True),
        sa.Column("consent_notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_speaker_profile_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_speaker_profile_workspace_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_speaker_profile")),
    )
    op.create_index(
        op.f("ix_speaker_profile_tenant_id"), "speaker_profile", ["tenant_id"], unique=False
    )
    op.create_table(
        "topic",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("locale", sa.String(length=8), nullable=False),
        sa.Column("pillar", sa.String(length=200), nullable=True),
        sa.Column("keywords", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("search_intent", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "PROPOSED",
                "APPROVED",
                "IN_PRODUCTION",
                "DONE",
                "DISCARDED",
                name="topic_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("planned_for", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_topic_tenant_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_topic_workspace_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_topic")),
    )
    op.create_index(op.f("ix_topic_tenant_id"), "topic", ["tenant_id"], unique=False)
    op.create_index("ix_topic_workspace_status", "topic", ["workspace_id", "status"], unique=False)
    op.create_table(
        "content_package",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("topic_id", sa.UUID(), nullable=True),
        sa.Column("brand_brief_id", sa.UUID(), nullable=True),
        sa.Column("parent_package_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("locale", sa.String(length=8), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PLANNED",
                "DRAFTING",
                "TEXT_REVIEW",
                "REJECTED",
                "MEDIA_GENERATING",
                "SELECTION",
                "SCHEDULED",
                "PUBLISHED",
                "MEASURING",
                "REFRESH",
                "FAILED",
                name="package_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "video_mode",
            sa.Enum("NONE", "VOICE", "FACE", name="video_mode", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column(
            "current_step",
            sa.Enum(
                "RESEARCHER",
                "STRATEGIST",
                "WRITER",
                "GEO_OPTIMIZER",
                "SEO_OPTIMIZER",
                "QA",
                "MARKETIZER",
                "TOPIC_PLANNER",
                name="pipeline_step",
                native_enum=False,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("qa_retry_count", sa.Integer(), nullable=False),
        sa.Column("qa_score", sa.Float(), nullable=True),
        sa.Column("gpu_seconds", sa.Float(), nullable=False),
        sa.Column("article", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["brand_brief_id"],
            ["brand_brief.id"],
            name=op.f("fk_content_package_brand_brief_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["parent_package_id"],
            ["content_package.id"],
            name=op.f("fk_content_package_parent_package_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_content_package_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["topic.id"],
            name=op.f("fk_content_package_topic_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_content_package_workspace_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_content_package")),
    )
    op.create_index(
        "ix_content_package_parent", "content_package", ["parent_package_id"], unique=False
    )
    op.create_index(
        op.f("ix_content_package_tenant_id"), "content_package", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_content_package_workspace_status",
        "content_package",
        ["workspace_id", "status"],
        unique=False,
    )
    op.create_table(
        "approval",
        sa.Column("package_id", sa.UUID(), nullable=False),
        sa.Column(
            "gate",
            sa.Enum("TEXT", "MEDIA", name="approval_gate", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column(
            "decision",
            sa.Enum(
                "APPROVED",
                "CHANGES_REQUESTED",
                "REJECTED",
                name="approval_decision",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column(
            "return_to_step",
            sa.Enum(
                "RESEARCHER",
                "STRATEGIST",
                "WRITER",
                "GEO_OPTIMIZER",
                "SEO_OPTIMIZER",
                "QA",
                "MARKETIZER",
                "TOPIC_PLANNER",
                name="pipeline_step",
                native_enum=False,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("decided_by_id", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_id"],
            ["app_user.id"],
            name=op.f("fk_approval_decided_by_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["content_package.id"],
            name=op.f("fk_approval_package_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_approval_tenant_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval")),
    )
    op.create_index("ix_approval_package_gate", "approval", ["package_id", "gate"], unique=False)
    op.create_index(op.f("ix_approval_tenant_id"), "approval", ["tenant_id"], unique=False)
    op.create_table(
        "step_run",
        sa.Column("package_id", sa.UUID(), nullable=False),
        sa.Column(
            "step",
            sa.Enum(
                "RESEARCHER",
                "STRATEGIST",
                "WRITER",
                "GEO_OPTIMIZER",
                "SEO_OPTIMIZER",
                "QA",
                "MARKETIZER",
                "TOPIC_PLANNER",
                name="pipeline_step",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "RUNNING",
                "SUCCEEDED",
                "FAILED",
                "SKIPPED",
                name="step_status",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("input_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("output_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("gpu_seconds", sa.Float(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["content_package.id"],
            name=op.f("fk_step_run_package_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_step_run_tenant_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_step_run")),
    )
    op.create_index("ix_step_run_package_step", "step_run", ["package_id", "step"], unique=False)
    op.create_index(op.f("ix_step_run_tenant_id"), "step_run", ["tenant_id"], unique=False)
    op.create_table(
        "variant",
        sa.Column("package_id", sa.UUID(), nullable=False),
        sa.Column(
            "channel",
            sa.Enum(
                "WORDPRESS",
                "TELEGRAM",
                "INSTAGRAM",
                "LINKEDIN",
                "X",
                "YOUTUBE",
                "APARAT",
                name="channel",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("ab_label", sa.String(length=8), nullable=True),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("visual_brief", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_selected", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["content_package.id"],
            name=op.f("fk_variant_package_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_variant_tenant_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_variant")),
    )
    op.create_index(
        "ix_variant_package_channel", "variant", ["package_id", "channel"], unique=False
    )
    op.create_index(op.f("ix_variant_tenant_id"), "variant", ["tenant_id"], unique=False)
    op.create_table(
        "media_asset",
        sa.Column("package_id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=True),
        sa.Column(
            "kind",
            sa.Enum(
                "IMAGE",
                "VIDEO",
                "AUDIO",
                "SUBTITLE",
                name="media_kind",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("gpu_seconds", sa.Float(), nullable=False),
        sa.Column("is_selected", sa.Boolean(), nullable=False),
        sa.Column("is_ai_labelled", sa.Boolean(), nullable=False),
        sa.Column("speaker_profile_id", sa.UUID(), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["content_package.id"],
            name=op.f("fk_media_asset_package_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["speaker_profile_id"],
            ["speaker_profile.id"],
            name=op.f("fk_media_asset_speaker_profile_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_media_asset_tenant_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["variant.id"],
            name=op.f("fk_media_asset_variant_id"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_asset")),
    )
    op.create_index(
        "ix_media_asset_package_kind", "media_asset", ["package_id", "kind"], unique=False
    )
    op.create_index(op.f("ix_media_asset_tenant_id"), "media_asset", ["tenant_id"], unique=False)
    op.create_table(
        "publication",
        sa.Column("package_id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=True),
        sa.Column(
            "channel",
            sa.Enum(
                "WORDPRESS",
                "TELEGRAM",
                "INSTAGRAM",
                "LINKEDIN",
                "X",
                "YOUTUBE",
                "APARAT",
                name="channel",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "SCHEDULED",
                "PUBLISHING",
                "PUBLISHED",
                "FAILED",
                "CANCELLED",
                name="publication_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("external_url", sa.String(length=2048), nullable=True),
        sa.Column("utm", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("operator_alerted", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["content_package.id"],
            name=op.f("fk_publication_package_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_publication_tenant_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["variant.id"],
            name=op.f("fk_publication_variant_id"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_publication")),
    )
    op.create_index("ix_publication_package", "publication", ["package_id"], unique=False)
    op.create_index(
        "ix_publication_scheduled", "publication", ["status", "scheduled_at"], unique=False
    )
    op.create_index(op.f("ix_publication_tenant_id"), "publication", ["tenant_id"], unique=False)
    op.create_table(
        "gpu_job",
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("package_id", sa.UUID(), nullable=True),
        sa.Column("step_run_id", sa.UUID(), nullable=True),
        sa.Column("media_asset_id", sa.UUID(), nullable=True),
        sa.Column(
            "kind",
            sa.Enum(
                "LLM_TEXT",
                "EMBEDDING",
                "IMAGE_FLUX",
                "VIDEO_WAN",
                "TTS",
                "TRANSCRIBE_WHISPER",
                "LIPSYNC_LATENTSYNC",
                "LIPSYNC_SADTALKER",
                name="gpu_job_kind",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "window",
            sa.Enum("TEXT", "MEDIA", name="gpu_window", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "LEASED",
                "RUNNING",
                "SUCCEEDED",
                "FAILED",
                "CANCELLED",
                name="gpu_job_status",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("nightly_only", sa.Boolean(), nullable=False),
        sa.Column("estimated_seconds", sa.Float(), nullable=False),
        sa.Column("gpu_seconds", sa.Float(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("batch_id", sa.UUID(), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_gpu_job_attempts_non_negative")),
        sa.CheckConstraint("estimated_seconds >= 0", name=op.f("ck_gpu_job_estimate_non_negative")),
        sa.ForeignKeyConstraint(
            ["media_asset_id"],
            ["media_asset.id"],
            name=op.f("fk_gpu_job_media_asset_id"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["content_package.id"],
            name=op.f("fk_gpu_job_package_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["step_run_id"],
            ["step_run.id"],
            name=op.f("fk_gpu_job_step_run_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_gpu_job_tenant_id"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_gpu_job_workspace_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gpu_job")),
    )
    op.create_index(op.f("ix_gpu_job_batch_id"), "gpu_job", ["batch_id"], unique=False)
    op.create_index(
        "ix_gpu_job_dispatch", "gpu_job", ["status", "window", "kind", "created_at"], unique=False
    )
    op.create_index("ix_gpu_job_lease", "gpu_job", ["status", "lease_expires_at"], unique=False)
    op.create_index(op.f("ix_gpu_job_tenant_id"), "gpu_job", ["tenant_id"], unique=False)
    op.create_index("ix_gpu_job_tenant_status", "gpu_job", ["tenant_id", "status"], unique=False)
    op.create_table(
        "metric_snapshot",
        sa.Column("publication_id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("captured_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("impressions", sa.Integer(), nullable=True),
        sa.Column("clicks", sa.Integer(), nullable=True),
        sa.Column("position", sa.Float(), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["publication_id"],
            ["publication.id"],
            name=op.f("fk_metric_snapshot_publication_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_metric_snapshot_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metric_snapshot")),
        sa.UniqueConstraint(
            "publication_id", "source", "captured_for", name="publication_source_day"
        ),
    )
    op.create_index(
        op.f("ix_metric_snapshot_tenant_id"), "metric_snapshot", ["tenant_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_metric_snapshot_tenant_id"), table_name="metric_snapshot")
    op.drop_table("metric_snapshot")
    op.drop_index("ix_gpu_job_tenant_status", table_name="gpu_job")
    op.drop_index(op.f("ix_gpu_job_tenant_id"), table_name="gpu_job")
    op.drop_index("ix_gpu_job_lease", table_name="gpu_job")
    op.drop_index("ix_gpu_job_dispatch", table_name="gpu_job")
    op.drop_index(op.f("ix_gpu_job_batch_id"), table_name="gpu_job")
    op.drop_table("gpu_job")
    op.drop_index(op.f("ix_publication_tenant_id"), table_name="publication")
    op.drop_index("ix_publication_scheduled", table_name="publication")
    op.drop_index("ix_publication_package", table_name="publication")
    op.drop_table("publication")
    op.drop_index(op.f("ix_media_asset_tenant_id"), table_name="media_asset")
    op.drop_index("ix_media_asset_package_kind", table_name="media_asset")
    op.drop_table("media_asset")
    op.drop_index(op.f("ix_variant_tenant_id"), table_name="variant")
    op.drop_index("ix_variant_package_channel", table_name="variant")
    op.drop_table("variant")
    op.drop_index(op.f("ix_step_run_tenant_id"), table_name="step_run")
    op.drop_index("ix_step_run_package_step", table_name="step_run")
    op.drop_table("step_run")
    op.drop_index(op.f("ix_approval_tenant_id"), table_name="approval")
    op.drop_index("ix_approval_package_gate", table_name="approval")
    op.drop_table("approval")
    op.drop_index("ix_content_package_workspace_status", table_name="content_package")
    op.drop_index(op.f("ix_content_package_tenant_id"), table_name="content_package")
    op.drop_index("ix_content_package_parent", table_name="content_package")
    op.drop_table("content_package")
    op.drop_index("ix_topic_workspace_status", table_name="topic")
    op.drop_index(op.f("ix_topic_tenant_id"), table_name="topic")
    op.drop_table("topic")
    op.drop_index(op.f("ix_speaker_profile_tenant_id"), table_name="speaker_profile")
    op.drop_table("speaker_profile")
    op.drop_index(op.f("ix_membership_tenant_id"), table_name="membership")
    op.drop_table("membership")
    op.drop_index(op.f("ix_channel_credential_tenant_id"), table_name="channel_credential")
    op.drop_table("channel_credential")
    op.drop_index(op.f("ix_brand_brief_tenant_id"), table_name="brand_brief")
    op.drop_table("brand_brief")
    op.drop_index(op.f("ix_workspace_tenant_id"), table_name="workspace")
    op.drop_table("workspace")
    op.drop_index(op.f("ix_gpu_quota_ledger_tenant_id"), table_name="gpu_quota_ledger")
    op.drop_index(op.f("ix_gpu_quota_ledger_day"), table_name="gpu_quota_ledger")
    op.drop_table("gpu_quota_ledger")
    op.drop_table("tenant")
    op.drop_table("gpu_window_state")
    op.drop_table("gpu_cost_estimate")
    op.drop_index(op.f("ix_app_user_email"), table_name="app_user")
    op.drop_table("app_user")
