"""Shared adapter error types and result envelope. Business logic only sees these, never vendor SDK errors."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class AdapterError(Exception):
    kind = "adapter_error"
    retryable = False

    def __init__(self, message: str, *, adapter: str = "", details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.adapter = adapter
        self.details = details or {}

    def to_dict(self) -> dict:
        return {"kind": self.kind, "message": self.message, "adapter": self.adapter, "details": self.details}


class AuthError(AdapterError):
    kind = "auth_failure"


class PermissionDenied(AdapterError):
    kind = "permission_denied"


class RateLimited(AdapterError):
    kind = "rate_limited"
    retryable = True


class AdapterTimeout(AdapterError):
    """The outcome is UNKNOWN. Callers must reconcile writes, never blind-retry."""

    kind = "timeout"
    retryable = True


class NotFoundError(AdapterError):
    kind = "not_found"


class InvalidInput(AdapterError):
    kind = "invalid_input"


class PartialData(AdapterError):
    kind = "partial_data"


class ServiceUnavailable(AdapterError):
    kind = "outage"
    retryable = True


class WritesDisabled(AdapterError):
    kind = "writes_disabled"


FAILURE_CLASSES: dict[str, type[AdapterError]] = {
    "auth_failure": AuthError,
    "permission_denied": PermissionDenied,
    "rate_limited": RateLimited,
    "timeout": AdapterTimeout,
    "not_found": NotFoundError,
    "missing_data": NotFoundError,
    "invalid_input": InvalidInput,
    "outage": ServiceUnavailable,
}


@dataclass
class AdapterResult(Generic[T]):
    data: T
    source: str  # LIVE (a test fake reports MOCK)
    adapter: str
    partial: bool = False
    warnings: list[str] = field(default_factory=list)
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def meta(self) -> dict[str, Any]:
        return {"source": self.source, "adapter": self.adapter, "partial": self.partial,
                "warnings": self.warnings, "fetched_at": self.fetched_at}
