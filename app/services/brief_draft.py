"""The questionnaire's saved state and the "guess it" flow.

The suggestion runs on the GPU queue like every other model call — the API
must never talk to vLLM directly, or the single-owner rule the whole of
handoff section 6 rests on stops holding. So the button is asynchronous: it
marks the draft ``running``, queues the work, and the panel polls.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.db.enums import SuggestionStatus
from app.db.models import BrandBrief, BriefDraft, Workspace
from app.services import questionnaire

logger = logging.getLogger(__name__)


def get_or_create_draft(
    session: Session, tenant_id: uuid.UUID, workspace_id: uuid.UUID
) -> BriefDraft:
    draft = session.scalars(
        select(BriefDraft).where(BriefDraft.workspace_id == workspace_id)
    ).one_or_none()
    if draft is not None:
        return draft

    if session.get(Workspace, workspace_id) is None:
        raise NotFoundError("workspace not found")

    draft = BriefDraft(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        answers={},
        suggestion_status=SuggestionStatus.IDLE,
    )
    session.add(draft)
    session.flush()
    return draft


def save_answers(draft: BriefDraft, answers: dict[str, Any]) -> dict[str, str]:
    """Merge answers into the draft, returning any problems found.

    Partial saves are the normal case, so a missing required answer is not
    an error here — only an unknown question or an invalid option is.
    """
    merged = {**draft.answers, **answers}
    problems = questionnaire.validate_answers(merged, require_complete=False)
    if problems:
        return problems

    # Assigning a new dict rather than mutating: SQLAlchemy does not track
    # in-place changes to a JSONB value.
    draft.answers = merged
    return {}


def accept_suggestions(draft: BriefDraft, question_ids: list[str]) -> list[str]:
    """Copy named suggestions into the answers.

    Explicit by design: a suggestion the customer never noticed is worse than
    a blank field, so nothing is merged automatically.
    """
    if not draft.suggestion:
        raise ConflictError("there is no suggestion to accept")

    answers = dict(draft.answers)
    accepted: list[str] = []
    for question_id in question_ids:
        if question_id not in questionnaire.BY_ID:
            continue
        value = draft.suggestion.get(question_id)
        if value in (None, "", [], {}):
            continue
        answers[question_id] = value
        accepted.append(question_id)

    draft.answers = answers
    return accepted


def mark_suggestion_running(draft: BriefDraft, website_url: str | None) -> None:
    if draft.suggestion_status is SuggestionStatus.RUNNING:
        raise ConflictError("a suggestion is already being prepared")
    draft.suggestion_status = SuggestionStatus.RUNNING
    draft.suggestion_error = None
    draft.suggestion_updated_at = datetime.now(UTC)
    if website_url:
        draft.website_url = website_url


def store_suggestion(draft: BriefDraft, suggestion: dict[str, Any]) -> None:
    draft.suggestion = suggestion
    draft.suggestion_status = SuggestionStatus.READY
    draft.suggestion_error = None
    draft.suggestion_updated_at = datetime.now(UTC)


def fail_suggestion(draft: BriefDraft, reason: str) -> None:
    draft.suggestion_status = SuggestionStatus.FAILED
    draft.suggestion_error = reason[:2000]
    draft.suggestion_updated_at = datetime.now(UTC)


def store_website(draft: BriefDraft, url: str, excerpt: str) -> None:
    draft.website_url = url
    draft.website_excerpt = excerpt
    draft.website_fetched_at = datetime.now(UTC)


def submit(session: Session, draft: BriefDraft, created_by: uuid.UUID | None) -> BrandBrief:
    """Turn a completed questionnaire into a new brand brief version.

    Raises :class:`~app.services.questionnaire.QuestionnaireError` listing
    every unanswered required question, so the wizard can highlight all of
    them at once.
    """
    from sqlalchemy import func, update

    data = questionnaire.answers_to_brief(draft.answers)

    highest = session.scalar(
        select(func.max(BrandBrief.version)).where(BrandBrief.workspace_id == draft.workspace_id)
    )
    session.execute(
        update(BrandBrief)
        .where(BrandBrief.workspace_id == draft.workspace_id)
        .values(is_active=False)
    )

    brief = BrandBrief(
        tenant_id=draft.tenant_id,
        workspace_id=draft.workspace_id,
        version=(highest or 0) + 1,
        is_active=True,
        data=data.model_dump(mode="json"),
        video_mode=data.video_mode,
        created_by_id=created_by,
    )
    session.add(brief)
    session.flush()

    draft.submitted_brief_id = brief.id
    # The workspace's configured languages follow the brief the customer
    # actually submitted, not whatever was guessed at sign-up.
    workspace = session.get(Workspace, draft.workspace_id)
    if workspace is not None:
        workspace.locales = list(data.locales)
        if data.locales and workspace.default_locale not in data.locales:
            workspace.default_locale = data.locales[0]

    session.flush()
    logger.info(
        "brand brief submitted",
        extra={"workspace_id": str(draft.workspace_id), "version": brief.version},
    )
    return brief


def as_payload(draft: BriefDraft) -> dict[str, Any]:
    """Everything the wizard needs to render its current state."""
    return {
        "answers": draft.answers,
        "completeness": questionnaire.completeness(draft.answers),
        "suggestion": draft.suggestion,
        "suggestion_status": draft.suggestion_status.value,
        "suggestion_error": draft.suggestion_error,
        "suggestion_updated_at": draft.suggestion_updated_at,
        "website_url": draft.website_url,
        "website_fetched_at": draft.website_fetched_at,
        "submitted_brief_id": draft.submitted_brief_id,
    }
