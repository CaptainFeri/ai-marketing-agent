"""GPU capacity accounting — handoff section 7, "volume follows capability".

The mechanism, in order:

1. every step records its real GPU time (``StepRun.gpu_seconds``,
   ``MediaAsset.gpu_seconds``, ``GpuJob.gpu_seconds``);
2. :func:`record_actual` folds that into a moving average per job kind;
3. :func:`allocate_day` splits the day's capacity between tenants by plan weight;
4. :func:`quota_status` turns the remainder into "how many packages of each
   shape can you still make today", which is what the panel shows;
5. raising capacity (a second GPU in phase 4) raises every allocation with no
   code change — only ``GPU_DAILY_CAPACITY_SECONDS`` moves.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import QuotaExceededError
from app.db.enums import GpuJobKind
from app.db.models import GpuCostEstimate, GpuQuotaLedger, Tenant
from app.db.tenancy import system_session
from app.schemas.gpu import QuotaAffordance, QuotaStatus

#: Starting points until phase 0 measures the real numbers on the 3090 Ti.
#: Every one of these is replaced by the moving average after a few runs.
DEFAULT_ESTIMATES: dict[GpuJobKind, float] = {
    GpuJobKind.LLM_TEXT: 75.0,  # one agent turn, ~1.5k output tokens
    GpuJobKind.EMBEDDING: 5.0,
    GpuJobKind.IMAGE_FLUX: 12.0,  # one 1024px image, FLUX.1-schnell
    GpuJobKind.VIDEO_WAN: 780.0,  # one 5s 720p clip, Wan 2.2 TI2V-5B
    GpuJobKind.TTS: 20.0,
    GpuJobKind.TRANSCRIBE_WHISPER: 30.0,
    GpuJobKind.LIPSYNC_LATENTSYNC: 450.0,  # 60s of talking head
    GpuJobKind.LIPSYNC_SADTALKER: 300.0,
}

#: How a finished package of each shape decomposes into GPU jobs.  Used only
#: to answer "how many can I still make today" — the real cost is whatever the
#: jobs actually take.
#:
#: NOTE: these recipes are a literal reading of the per-job table in handoff
#: section 7, and they do not reproduce its "15 to 30 full packages per day"
#: summary.  The components add up to roughly 10 minutes for a ``voice``
#: package, which would be ~110 per day at 20h of capacity, not 15-30.  The
#: summary presumably assumes several languages, more media per package, QA
#: retries and window-switch overhead.  Phase 0 measures the real numbers and
#: settles it; until then the panel will read optimistically.
PACKAGE_RECIPES: dict[str, dict[GpuJobKind, int]] = {
    # Six text agents plus the marketizer.
    "text_only": {GpuJobKind.LLM_TEXT: 7, GpuJobKind.EMBEDDING: 2},
    "text_and_images": {
        GpuJobKind.LLM_TEXT: 7,
        GpuJobKind.EMBEDDING: 2,
        GpuJobKind.IMAGE_FLUX: 4,
    },
    "video_voice": {
        GpuJobKind.LLM_TEXT: 7,
        GpuJobKind.EMBEDDING: 2,
        GpuJobKind.IMAGE_FLUX: 4,
        GpuJobKind.TTS: 1,
        GpuJobKind.TRANSCRIBE_WHISPER: 1,
    },
    "video_face": {
        GpuJobKind.LLM_TEXT: 7,
        GpuJobKind.EMBEDDING: 2,
        GpuJobKind.IMAGE_FLUX: 4,
        GpuJobKind.TTS: 1,
        GpuJobKind.TRANSCRIBE_WHISPER: 1,
        GpuJobKind.LIPSYNC_LATENTSYNC: 1,
    },
    "video_ai_clip": {
        GpuJobKind.LLM_TEXT: 7,
        GpuJobKind.EMBEDDING: 2,
        GpuJobKind.IMAGE_FLUX: 2,
        GpuJobKind.TTS: 1,
        GpuJobKind.TRANSCRIBE_WHISPER: 1,
        GpuJobKind.VIDEO_WAN: 2,
    },
}

#: Weight of a new sample in the moving average.  Low enough that one slow run
#: does not move the estimate far, high enough to track a model change.
EWMA_ALPHA = 0.2

ANY_LOCALE = "*"


# --------------------------------------------------------------------------
# cost estimates
# --------------------------------------------------------------------------
def estimate_seconds(session: Session, kind: GpuJobKind, locale: str = ANY_LOCALE) -> float:
    """Best known GPU cost for one job of ``kind``.

    Falls back from the locale-specific average, to the locale-agnostic one,
    to the hard-coded starting point.
    """
    rows = session.scalars(
        select(GpuCostEstimate).where(
            GpuCostEstimate.kind == kind,
            GpuCostEstimate.locale.in_({locale, ANY_LOCALE}),
        )
    ).all()
    by_locale = {row.locale: row for row in rows}
    for key in (locale, ANY_LOCALE):
        row = by_locale.get(key)
        if row is not None and row.samples > 0:
            return row.ewma_seconds
    return DEFAULT_ESTIMATES[kind]


def record_actual(
    session: Session, kind: GpuJobKind, seconds: float, locale: str = ANY_LOCALE
) -> None:
    """Fold a measured run into the moving average (section 7, step 2)."""
    if seconds <= 0:
        return
    seed = DEFAULT_ESTIMATES[kind]
    # One statement so concurrent GPU workers cannot lose an update; the
    # EWMA is computed against the value already stored.
    statement = (
        pg_insert(GpuCostEstimate)
        .values(
            kind=kind,
            locale=locale,
            ewma_seconds=EWMA_ALPHA * seconds + (1 - EWMA_ALPHA) * seed,
            samples=1,
            last_seconds=seconds,
        )
        .on_conflict_do_update(
            index_elements=["kind", "locale"],
            set_={
                "ewma_seconds": (
                    EWMA_ALPHA * seconds
                    + (1 - EWMA_ALPHA) * GpuCostEstimate.__table__.c.ewma_seconds
                ),
                "samples": GpuCostEstimate.__table__.c.samples + 1,
                "last_seconds": seconds,
            },
        )
    )
    session.execute(statement)


def estimate_package_seconds(session: Session, recipe: str, locale: str = ANY_LOCALE) -> float:
    counts = PACKAGE_RECIPES[recipe]
    return sum(estimate_seconds(session, kind, locale) * n for kind, n in counts.items())


# --------------------------------------------------------------------------
# daily allocation
# --------------------------------------------------------------------------
def allocate_day(session: Session, day: date, capacity_seconds: float | None = None) -> int:
    """Create or refresh the ledger row of every active tenant for ``day``.

    Runs over a cross-tenant session because the split depends on the sum of
    all weights.  Consumption already booked is preserved — only the
    allocation is recomputed, so a plan change mid-day takes effect at once.

    Returns the number of tenants that received an allocation.
    """
    capacity = float(
        capacity_seconds if capacity_seconds is not None else settings.gpu_daily_capacity_seconds
    )
    tenants = session.scalars(select(Tenant).where(Tenant.is_active.is_(True))).all()
    total_weight = sum(max(1, t.quota_weight) for t in tenants)
    if total_weight == 0:
        return 0

    for tenant in tenants:
        weight = max(1, tenant.quota_weight)
        allocated = capacity * weight / total_weight
        session.execute(
            pg_insert(GpuQuotaLedger)
            .values(
                tenant_id=tenant.id,
                day=day,
                allocated_seconds=allocated,
                reserved_seconds=0.0,
                consumed_seconds=0.0,
                weight=weight,
                capacity_seconds=capacity,
            )
            .on_conflict_do_update(
                index_elements=["tenant_id", "day"],
                # Consumed and reserved are deliberately left alone.
                set_={
                    "allocated_seconds": allocated,
                    "weight": weight,
                    "capacity_seconds": capacity,
                },
            )
        )
    return len(tenants)


def _ledger_for(session: Session, tenant_id: uuid.UUID, day: date) -> GpuQuotaLedger:
    """Fetch today's ledger row, locking it for the caller's transaction.

    If the nightly allocator has not run yet — a tenant created this morning,
    or a fresh install — the allocation is computed on the spot over a system
    session, because the split needs every tenant's weight.
    """
    row = session.scalars(
        select(GpuQuotaLedger)
        .where(GpuQuotaLedger.tenant_id == tenant_id, GpuQuotaLedger.day == day)
        .with_for_update()
    ).one_or_none()
    if row is not None:
        return row

    with system_session() as admin:
        allocate_day(admin, day)

    row = session.scalars(
        select(GpuQuotaLedger)
        .where(GpuQuotaLedger.tenant_id == tenant_id, GpuQuotaLedger.day == day)
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise QuotaExceededError(
            "no GPU allocation exists for this tenant today; the tenant may be inactive",
            details={"tenant_id": str(tenant_id), "day": day.isoformat()},
        )
    return row


# --------------------------------------------------------------------------
# reserve / consume / release
# --------------------------------------------------------------------------
def reserve(
    session: Session, tenant_id: uuid.UUID, seconds: float, day: date | None = None
) -> GpuQuotaLedger:
    """Hold ``seconds`` of the tenant's daily share.

    Raises :class:`QuotaExceededError` when the day's share is spent; the
    caller is expected to queue the work for tomorrow rather than fail it
    (handoff section 7, step 4).
    """
    day = day or date.today()
    row = _ledger_for(session, tenant_id, day)
    if seconds > row.remaining_seconds:
        raise QuotaExceededError(
            "today's GPU quota is exhausted for this tenant",
            details={
                "requested_seconds": seconds,
                "remaining_seconds": row.remaining_seconds,
                "day": day.isoformat(),
            },
        )
    row.reserved_seconds += seconds
    return row


def release(
    session: Session, tenant_id: uuid.UUID, seconds: float, day: date | None = None
) -> GpuQuotaLedger:
    """Give back a reservation whose job was cancelled."""
    day = day or date.today()
    row = _ledger_for(session, tenant_id, day)
    row.reserved_seconds = max(0.0, row.reserved_seconds - seconds)
    return row


def consume(
    session: Session,
    tenant_id: uuid.UUID,
    reserved_seconds: float,
    actual_seconds: float,
    day: date | None = None,
) -> GpuQuotaLedger:
    """Turn a reservation into real consumption once the job has finished.

    The actual time is booked even when it overshoots the reservation — the
    GPU really was busy — which simply leaves the tenant less room today.
    """
    day = day or date.today()
    row = _ledger_for(session, tenant_id, day)
    row.reserved_seconds = max(0.0, row.reserved_seconds - reserved_seconds)
    row.consumed_seconds += max(0.0, actual_seconds)
    return row


# --------------------------------------------------------------------------
# panel view
# --------------------------------------------------------------------------
def quota_status(
    session: Session,
    tenant_id: uuid.UUID,
    day: date | None = None,
    locale: str = ANY_LOCALE,
    recipes: Iterable[str] | None = None,
) -> QuotaStatus:
    day = day or date.today()
    row = _ledger_for(session, tenant_id, day)
    remaining = row.remaining_seconds

    affordances = []
    for name in recipes or PACKAGE_RECIPES:
        each = estimate_package_seconds(session, name, locale)
        affordances.append(
            QuotaAffordance(
                label=name,
                estimated_seconds_each=round(each, 1),
                affordable_today=int(remaining // each) if each > 0 else 0,
            )
        )

    return QuotaStatus(
        tenant_id=tenant_id,
        day=day,
        capacity_seconds=row.capacity_seconds,
        weight=row.weight,
        allocated_seconds=row.allocated_seconds,
        reserved_seconds=row.reserved_seconds,
        consumed_seconds=row.consumed_seconds,
        remaining_seconds=remaining,
        affordances=affordances,
    )
