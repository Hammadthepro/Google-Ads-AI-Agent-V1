"""HTTP helpers shared by the research adapters: one place that classifies provider failures."""
from __future__ import annotations

import threading
import time

import httpx

from app.adapters.base import (AdapterError, AdapterTimeout, AuthError, InvalidInput, InvalidResponse, NotFoundError, PermissionDenied,
                               QuotaExceeded, RateLimited, ServiceUnavailable)

USER_AGENT = "GoogleAdsAIAgent-Research/1.0 (+https://github.com/Hammadthepro/Google-Ads-AI-Agent-V1)"
_last: dict[str, float] = {}
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def throttle(provider: str, min_interval: float) -> None:
    """At most one request per `min_interval` seconds per provider in this process (public infrastructure usage policies)."""
    if min_interval <= 0:
        return
    with _guard:
        lock = _locks.setdefault(provider, threading.Lock())
    with lock:
        wait = _last.get(provider, 0) + min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last[provider] = time.monotonic()


def classify(resp: httpx.Response, adapter: str, *, quota_statuses: tuple[int, ...] = ()) -> None:
    """Raise the matching AdapterError for a non-success response. A 2xx returns normally."""
    code = resp.status_code
    if 200 <= code < 300:
        return
    detail = {"status": code}
    if code in quota_statuses:
        raise QuotaExceeded(f"{adapter}: plan or credit limit reached (HTTP {code})", adapter=adapter, details=detail)
    if code == 401:
        raise AuthError(f"{adapter}: the API key was rejected (HTTP 401)", adapter=adapter, details=detail)
    if code == 403:
        raise PermissionDenied(f"{adapter}: access denied (HTTP 403)", adapter=adapter, details=detail)
    if code == 404:
        raise NotFoundError(f"{adapter}: not found (HTTP 404)", adapter=adapter, details=detail)
    if code == 429:
        raise RateLimited(f"{adapter}: rate limited (HTTP 429)", adapter=adapter, details=detail)
    if code >= 500:
        raise ServiceUnavailable(f"{adapter}: provider error (HTTP {code})", adapter=adapter, details=detail)
    raise InvalidInput(f"{adapter}: request rejected (HTTP {code})", adapter=adapter, details=detail)


def request(client: httpx.Client, method: str, url: str, adapter: str, **kw) -> httpx.Response:
    """Send a request; network problems become AdapterTimeout / ServiceUnavailable instead of raw httpx errors."""
    headers = {"User-Agent": USER_AGENT, **kw.pop("headers", {})}
    try:
        return client.request(method, url, headers=headers, **kw)
    except httpx.TimeoutException as exc:
        raise AdapterTimeout(f"{adapter}: the request timed out", adapter=adapter) from exc
    except httpx.HTTPError as exc:
        raise ServiceUnavailable(f"{adapter}: network error ({type(exc).__name__})", adapter=adapter, details={"network": True}) from exc


def json_of(resp: httpx.Response, adapter: str):
    try:
        return resp.json()
    except ValueError as exc:
        raise InvalidResponse(f"{adapter}: the response was not valid JSON", adapter=adapter) from exc


__all__ = ["USER_AGENT", "throttle", "classify", "request", "json_of", "AdapterError"]
