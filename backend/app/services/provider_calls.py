"""Every research-provider request goes through `call()`:

* identical requests within the cache lifetime are answered from `provider_cache` (public servers and credit budgets are respected);
* metered providers can have a per-audit budget; once spent, the provider is NOT called and BudgetExhausted is raised;
* every request, cached or not, is written to `provider_calls`, which is the usage ledger shown on the Integrations page;
* failures are recorded with their class and re-raised: nothing is turned into an empty success.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any, Callable

from sqlalchemy import func, select

from app.adapters.base import AdapterError, BudgetExhausted
from app.core import clock
from app.core.util import redact
from app.db.session import session_scope
from app.models import ProviderCache, ProviderCall
from app.services import research_events


def cache_key(request: dict) -> str:
    return hashlib.sha256(json.dumps(request, sort_keys=True, default=str).encode()).hexdigest()


def _log(provider: str, operation: str, *, ok: bool, cached: bool, cost: float = 0, error: AdapterError | None = None) -> None:
    with session_scope() as db:
        db.add(ProviderCall(provider=provider, operation=operation, ok=ok, cached=cached, cost_units=cost,
                            error_kind=error.kind if error else "", error=(error.message[:500] if error else ""),
                            audit_report_id=research_events.current_report_id(), created_at=clock.now()))


def spent_this_audit(provider: str) -> int:
    rid = research_events.current_report_id()
    if not rid:
        return 0
    with session_scope() as db:
        return db.scalar(select(func.count(ProviderCall.id)).where(ProviderCall.provider == provider, ProviderCall.audit_report_id == rid,
                                                                   ProviderCall.cached.is_(False))) or 0


def is_cached(provider: str, operation: str, request: dict) -> bool:
    """Whether `call()` would answer this request from the cache (so it costs nothing). Used to plan metered batches."""
    key = cache_key({"op": operation, **request})
    with session_scope() as db:
        hit = db.scalar(select(ProviderCache.expires_at).where(ProviderCache.provider == provider, ProviderCache.cache_key == key))
    return bool(hit and hit > clock.now())


def call(provider: str, operation: str, request: dict, fn: Callable[[], Any], *, ttl: timedelta = timedelta(days=7),
         cost: float | Callable[[Any], float] = 0, budget: int | None = None, meta: bool = False) -> Any:
    """With `meta=True` returns (value, {"cached": bool, "cached_at": iso | None}) so callers never present a cached answer as new."""
    key = cache_key({"op": operation, **request})
    now = clock.now()
    with session_scope() as db:
        hit = db.scalar(select(ProviderCache).where(ProviderCache.provider == provider, ProviderCache.cache_key == key))
        if hit is not None and hit.expires_at > now:
            response = hit.response.get("value")
            _log(provider, operation, ok=True, cached=True)
            return (response, {"cached": True, "cached_at": hit.created_at.isoformat() if hit.created_at else None}) if meta else response
    if budget is not None and research_events.current_report_id() and spent_this_audit(provider) >= budget:
        err = BudgetExhausted(f"The per-audit budget of {budget} {provider} request(s) is used up; no further requests were sent.", adapter=provider)
        _log(provider, operation, ok=False, cached=False, error=err)
        raise err
    try:
        value = fn()
    except AdapterError as exc:
        _log(provider, operation, ok=False, cached=False, error=exc)
        raise
    units = cost(value) if callable(cost) else cost
    with session_scope() as db:
        row = db.scalar(select(ProviderCache).where(ProviderCache.provider == provider, ProviderCache.cache_key == key))
        if row is None:
            row = ProviderCache(provider=provider, cache_key=key)
            db.add(row)
        row.request, row.response, row.created_at, row.expires_at = redact(request), {"value": value}, now, now + ttl
    _log(provider, operation, ok=True, cached=False, cost=float(units or 0))
    return (value, {"cached": False, "cached_at": None}) if meta else value


def usage(provider: str, *, days: int = 30) -> dict:
    since = clock.now() - timedelta(days=days)
    with session_scope() as db:
        rows = db.execute(select(ProviderCall.cached, ProviderCall.ok, func.count(ProviderCall.id), func.coalesce(func.sum(ProviderCall.cost_units), 0))
                          .where(ProviderCall.provider == provider, ProviderCall.created_at >= since)
                          .group_by(ProviderCall.cached, ProviderCall.ok)).all()
        last_error = db.scalar(select(ProviderCall).where(ProviderCall.provider == provider, ProviderCall.ok.is_(False))
                               .order_by(ProviderCall.created_at.desc()).limit(1))
    out = {"days": days, "requests_sent": 0, "served_from_cache": 0, "failed": 0, "cost_units": 0.0}
    for cached, ok, n, cost in rows:
        if cached:
            out["served_from_cache"] += n
        else:
            out["requests_sent"] += n
            out["cost_units"] += float(cost)
            if not ok:
                out["failed"] += n
    out["last_error"] = ({"kind": last_error.error_kind, "message": last_error.error, "at": last_error.created_at.isoformat()} if last_error else None)
    return out
