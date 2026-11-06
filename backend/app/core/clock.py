"""Application clock. In TEST_MODE the clock can be advanced (SimulatedClock) to drive time-based mock scenarios."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.core.config import get_settings

_KEY = "mock_clock"


def _offset_hours() -> float:
    if not get_settings().TEST_MODE:
        return 0.0
    from app.db.session import session_scope
    from app.models import SystemSetting

    with session_scope() as db:
        row = db.get(SystemSetting, _KEY)
        return float((row.value or {}).get("offset_hours", 0)) if row else 0.0


def now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=_offset_hours())


def today() -> date:
    return now().date()


def advance(hours: float) -> datetime:
    settings = get_settings()
    if not settings.TEST_MODE:
        raise RuntimeError("The simulated clock can only be advanced in TEST_MODE")
    from app.db.session import session_scope
    from app.models import SystemSetting

    with session_scope() as db:
        row = db.get(SystemSetting, _KEY)
        current = float((row.value or {}).get("offset_hours", 0)) if row else 0.0
        new_val = {"offset_hours": current + hours}
        if row:
            row.value = new_val
        else:
            db.add(SystemSetting(key=_KEY, value=new_val))
    return now()


def reset() -> None:
    from app.db.session import session_scope
    from app.models import SystemSetting

    with session_scope() as db:
        row = db.get(SystemSetting, _KEY)
        if row:
            row.value = {"offset_hours": 0}
