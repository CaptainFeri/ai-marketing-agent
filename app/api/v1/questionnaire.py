"""The brand questionnaire wizard (handoff phase 1, week 3).

About twenty-five questions, a "guess it" button that reads the customer's own
site, and a submit that produces a versioned brand brief.

The guess is asynchronous. It has to be: the model runs on the GPU queue, and
letting the API call vLLM directly would put a second process on the card,
which is the one thing handoff section 6 exists to prevent.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import Principal, assert_workspace_role, get_db, get_principal
from app.db.enums import Role
from app.schemas.brief import BrandBriefOut
from app.schemas.questionnaire import (
    AcceptRequest,
    AcceptResponse,
    AnswersUpdate,
    GuessRequest,
    QuestionnaireState,
)
from app.services import brief_draft as draft_service
from app.services import questionnaire
from app.services.questionnaire import QuestionnaireError

router = APIRouter(prefix="/workspaces/{workspace_id}/questionnaire", tags=["questionnaire"])


def _state(draft) -> QuestionnaireState:
    return QuestionnaireState(
        catalogue=questionnaire.catalogue(), **draft_service.as_payload(draft)
    )


@router.get("", response_model=QuestionnaireState)
def get_questionnaire(
    workspace_id: uuid.UUID,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> QuestionnaireState:
    """The questions and whatever has been answered so far.

    Also how the last "guess it" is doing — the panel polls this rather than
    holding a request open while the GPU queue gets to it.
    """
    assert_workspace_role(principal, workspace_id, Role.VIEWER)
    draft = draft_service.get_or_create_draft(session, principal.tenant_id, workspace_id)
    return _state(draft)


@router.put("", response_model=QuestionnaireState)
def save_answers(
    workspace_id: uuid.UUID,
    payload: AnswersUpdate,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> QuestionnaireState:
    """Save part of the questionnaire.

    Partial saves are the normal case, so an unanswered required question is
    not an error here — only an unknown question or an invalid option is.
    """
    assert_workspace_role(principal, workspace_id, Role.EDITOR)
    draft = draft_service.get_or_create_draft(session, principal.tenant_id, workspace_id)
    problems = draft_service.save_answers(draft, payload.answers)
    if problems:
        raise QuestionnaireError("some answers could not be saved", details={"problems": problems})
    session.flush()
    return _state(draft)


@router.post("/guess", response_model=QuestionnaireState, status_code=status.HTTP_202_ACCEPTED)
def guess(
    workspace_id: uuid.UUID,
    payload: GuessRequest,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> QuestionnaireState:
    """Ask the assistant to draft the answerable questions.

    Returns immediately with ``suggestion_status: running``; poll ``GET``
    until it reads ``ready`` or ``failed``. Nothing is written into the
    customer's own answers — see ``POST /accept``.
    """
    assert_workspace_role(principal, workspace_id, Role.EDITOR)
    draft = draft_service.get_or_create_draft(session, principal.tenant_id, workspace_id)

    url = payload.website_url or draft.website_url or draft.answers.get("website")
    draft_service.mark_suggestion_running(draft, url)
    session.flush()

    from app.worker.tasks.pipeline import suggest_brief

    suggest_brief.delay(
        str(principal.tenant_id),
        str(workspace_id),
        url,
        payload.refresh_website,
    )
    return _state(draft)


@router.post("/accept", response_model=AcceptResponse)
def accept(
    workspace_id: uuid.UUID,
    payload: AcceptRequest,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> AcceptResponse:
    """Copy named suggestions into the answers.

    Explicit rather than automatic: a suggestion the customer never looked at
    is worse than a blank field.
    """
    assert_workspace_role(principal, workspace_id, Role.EDITOR)
    draft = draft_service.get_or_create_draft(session, principal.tenant_id, workspace_id)
    accepted = draft_service.accept_suggestions(draft, payload.question_ids)
    session.flush()
    return AcceptResponse(accepted=accepted, **draft_service.as_payload(draft))


@router.post("/submit", response_model=BrandBriefOut, status_code=status.HTTP_201_CREATED)
def submit(
    workspace_id: uuid.UUID,
    session: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> BrandBriefOut:
    """Turn the completed questionnaire into a new brand brief version.

    Every unanswered required question is listed at once, so the wizard can
    highlight all of them rather than one per attempt.
    """
    assert_workspace_role(principal, workspace_id, Role.EDITOR)
    draft = draft_service.get_or_create_draft(session, principal.tenant_id, workspace_id)
    brief = draft_service.submit(session, draft, principal.user_id)
    return BrandBriefOut.model_validate(brief)
