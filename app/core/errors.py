"""Domain errors mapped to HTTP responses in ``app.main``."""

from __future__ import annotations


class AppError(Exception):
    """Base class for errors that are safe to show to an API client."""

    status_code = 400
    code = "app_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class PermissionDeniedError(AppError):
    status_code = 403
    code = "permission_denied"


class AuthenticationError(AppError):
    status_code = 401
    code = "unauthenticated"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class QuotaExceededError(AppError):
    """The tenant asked for more GPU time than its daily share allows."""

    status_code = 429
    code = "quota_exceeded"


class InvalidStateError(AppError):
    """The content package is not in a state where this transition is legal."""

    status_code = 409
    code = "invalid_state"
