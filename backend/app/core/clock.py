"""Application clock: real UTC time. (There is no simulated clock; tests that need a fixed time patch `now`.)"""
from __future__ import annotations

from datetime import date, datetime, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)


def today() -> date:
    return now().date()
