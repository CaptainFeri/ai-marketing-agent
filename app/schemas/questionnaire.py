"""Questionnaire payloads."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AnswersUpdate(BaseModel):
    """A partial save. Only the questions present are touched."""

    answers: dict[str, Any] = Field(default_factory=dict)


class GuessRequest(BaseModel):
    """Ask the assistant to draft the answerable questions.

    ``website_url`` is optional: without it the assistant works from whatever
    the customer has already answered, which is thinner but still useful.
    """

    website_url: str | None = Field(default=None, max_length=2048)
    #: Re-read the site even if it was fetched recently.
    refresh_website: bool = False


class AcceptRequest(BaseModel):
    question_ids: list[str] = Field(min_length=1, max_length=40)


class DraftState(BaseModel):
    answers: dict[str, Any]
    completeness: dict[str, Any]
    suggestion: dict[str, Any] | None
    suggestion_status: str
    suggestion_error: str | None
    suggestion_updated_at: datetime | None
    website_url: str | None
    website_fetched_at: datetime | None
    submitted_brief_id: uuid.UUID | None


class QuestionnaireState(DraftState):
    """The catalogue and the draft together — one call to render the wizard."""

    catalogue: dict[str, Any]


class AcceptResponse(DraftState):
    accepted: list[str]
