"""ORM models.  Importing this package registers every table on ``Base``."""

from app.db.base import Base
from app.db.models.content import (
    Approval,
    BrandBrief,
    BriefDraft,
    ContentPackage,
    StepRun,
    Topic,
    Variant,
)
from app.db.models.gpu import (
    GpuCostEstimate,
    GpuJob,
    GpuQuotaLedger,
    GpuWindowState,
)
from app.db.models.media import MediaAsset, SpeakerProfile
from app.db.models.publishing import (
    ABTestResult,
    AnalyticsCredential,
    ChannelCredential,
    MetricSnapshot,
    Publication,
)
from app.db.models.tenancy import Membership, Tenant, User, Workspace

#: Tables that carry ``tenant_id`` and therefore need a row level security
#: policy.  ``tests/test_tenant_isolation.py`` asserts this list matches the
#: metadata, so a new tenant table cannot be added without a policy.
TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "membership",
    "workspace",
    "brand_brief",
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
    "ab_test_result",
    "channel_credential",
    "analytics_credential",
    "gpu_job",
    "gpu_quota_ledger",
)

__all__ = [
    "ABTestResult",
    "AnalyticsCredential",
    "Approval",
    "Base",
    "BrandBrief",
    "BriefDraft",
    "ChannelCredential",
    "ContentPackage",
    "GpuCostEstimate",
    "GpuJob",
    "GpuQuotaLedger",
    "GpuWindowState",
    "MediaAsset",
    "Membership",
    "MetricSnapshot",
    "Publication",
    "SpeakerProfile",
    "StepRun",
    "TENANT_SCOPED_TABLES",
    "Tenant",
    "Topic",
    "User",
    "Variant",
    "Workspace",
]
