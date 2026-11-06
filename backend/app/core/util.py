"""Small shared helpers: ids, canonical hashing, money, redaction."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

MICROS = 1_000_000


def new_id() -> str:
    return uuid.uuid4().hex


def _default(o: Any):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return str(o)
    raise TypeError(f"Not serialisable: {type(o)}")


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_default, ensure_ascii=False)


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def to_micros(amount: float | int | Decimal) -> int:
    return int(round(float(amount) * MICROS))


def from_micros(micros: int | None) -> float:
    return round((micros or 0) / MICROS, 2)


_SECRET_KEYS = re.compile(r"(token|secret|password|api[_-]?key|authorization|refresh|cookie)", re.I)
_SECRET_VALUES = re.compile(r"(gsk_[A-Za-z0-9]{8,}|ya29\.[A-Za-z0-9._-]+|1//0[A-Za-z0-9_-]{10,}|AIza[0-9A-Za-z_-]{20,})")


def redact(value: Any) -> Any:
    """Recursively redact secret-looking keys and values (used for logs and stored responses)."""
    if isinstance(value, dict):
        return {k: ("***REDACTED***" if _SECRET_KEYS.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _SECRET_VALUES.sub("***REDACTED***", value)
    return value


def fingerprint(secret: str) -> str:
    if not secret:
        return ""
    return hashlib.sha256(secret.encode()).hexdigest()[:12]


def normalize_term(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()
