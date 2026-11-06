from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.util import new_id

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class IdMixin:
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)


class TimestampMixin(IdMixin):
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


# String enums (validated in Python; stored as strings so migrations stay simple)
class Role:
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    CAMPAIGN_MANAGER = "CAMPAIGN_MANAGER"
    ANALYST = "ANALYST"
    ALL = (SUPER_ADMIN, ADMIN, CAMPAIGN_MANAGER, ANALYST)


class SourceType:
    MOCK = "MOCK"
    LIVE = "LIVE"
    MIXED = "MIXED"

    @staticmethod
    def combine(*sources: str | None) -> str:
        s = {x for x in sources if x}
        if not s:
            return SourceType.MOCK
        if len(s) == 1:
            return s.pop()
        return SourceType.MIXED
