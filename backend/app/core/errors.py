"""Domain errors mapped to safe HTTP responses."""
from __future__ import annotations


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"


class SafetyBlocked(AppError):
    """Raised by the WriteGuard. Never caught silently."""

    status_code = 423
    code = "safety_blocked"


class IntegrationNotConfigured(AppError):
    """A feature needs an integration whose credentials are missing or disabled. There is NO fallback: the caller must
    surface this to the user (HTTP 424) so they can configure and test the integration on the Integrations page."""

    status_code = 424
    code = "integration_not_configured"

    def __init__(self, provider: str, label: str, *, missing: list[str] | None = None, disabled: bool = False):
        self.provider, self.label, self.missing = provider, label, missing or []
        why = "is disabled" if disabled else "is not configured"
        super().__init__(f"{label} integration {why}. Please configure and test your connection on the Integrations page.",
                         details={"provider": provider, "missing": self.missing, "disabled": disabled,
                                  "action": "configure_integration", "path": "/integrations"})
