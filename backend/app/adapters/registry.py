"""The ONLY place that chooses mock vs real implementations.

Real implementations are chosen only by explicit *_MODE / provider settings — never because credentials exist.
In real mode, missing credentials raise at first use (no silent fallback to mock).
"""
from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings


@lru_cache
def google_ads():
    """Real only when the operating mode (Integrations page) is read_only/execution — never inferred from credentials."""
    from app.adapters.google_ads.mock import MockGoogleAdsAdapter
    from app.adapters.google_ads.real import RealGoogleAdsAdapter
    from app.services import integrations_service
    return RealGoogleAdsAdapter() if integrations_service.operating_mode() != "mock" else MockGoogleAdsAdapter()


@lru_cache
def keyword_research():
    from app.adapters.keyword_research import GoogleKeywordPlannerAdapter, MockKeywordResearchAdapter
    from app.services import integrations_service
    if integrations_service.operating_mode() != "mock":
        return GoogleKeywordPlannerAdapter(google_ads())
    return MockKeywordResearchAdapter()


@lru_cache
def places():
    from app.adapters.places import MockGooglePlacesAdapter, RealGooglePlacesAdapter
    from app.services import integrations_service
    real = integrations_service.resolve("places")["_enabled"] or get_settings().mode_for("PLACES") == "real"
    return RealGooglePlacesAdapter() if real else MockGooglePlacesAdapter()


@lru_cache
def pagespeed():
    from app.adapters.pagespeed import MockPageSpeedAdapter, RealPageSpeedAdapter
    from app.services import integrations_service
    real = integrations_service.resolve("pagespeed")["_enabled"] or get_settings().mode_for("PAGESPEED") == "real"
    return RealPageSpeedAdapter() if real else MockPageSpeedAdapter()


@lru_cache
def crawler():
    from app.adapters.crawler import FixtureCrawlerAdapter, LiveCrawlerAdapter
    from app.services import integrations_service
    v = integrations_service.resolve("crawler")
    live = (v["_enabled"] and v.get("mode") == "live") or get_settings().CRAWLER_MODE == "live"
    return LiveCrawlerAdapter() if live else FixtureCrawlerAdapter()


@lru_cache
def ai():
    from app.adapters.ai import GroqAIProvider, MockAIProvider
    """The internal AI (Groq when enabled via Integrations or env, else the deterministic mock)."""
    from app.services import integrations_service
    s = get_settings()
    if integrations_service.resolve("groq")["_enabled"] or (s.AI_PROVIDER == "groq" and s.GROQ_ENABLED):
        return GroqAIProvider()
    return MockAIProvider()


@lru_cache
def claude_ai():
    """The independent second AI. Falls back to the mock (labelled MOCK) when Claude is not enabled."""
    from app.adapters.ai import MockAIProvider
    from app.adapters.ai.claude import ClaudeAIProvider
    from app.services import integrations_service
    v = integrations_service.resolve("claude")
    if v["_enabled"]:
        return ClaudeAIProvider(v.get("api_key", ""), v.get("model", ""))
    return MockAIProvider(variant="b")


@lru_cache
def auth():
    from app.adapters.auth import GoogleOIDCAuthenticationProvider, MockAuthenticationProvider
    return GoogleOIDCAuthenticationProvider() if get_settings().AUTH_PROVIDER == "google" else MockAuthenticationProvider()


@lru_cache
def notifier():
    from app.adapters.notifications import EmailNotifier, MockNotifier
    return EmailNotifier() if get_settings().mode_for("NOTIFIER") == "real" else MockNotifier()


ALL = {"google_ads": google_ads, "keyword_research": keyword_research, "places": places, "pagespeed": pagespeed,
       "crawler": crawler, "ai": ai, "claude_ai": claude_ai, "auth": auth, "notifier": notifier}


def reset() -> None:
    for fn in ALL.values():
        fn.cache_clear()


_overrides: dict = {}


def override(name: str, adapter) -> None:
    """Test hook: replace an adapter instance."""
    ALL[name].cache_clear()
    _overrides[name] = adapter


def get(name: str):
    return _overrides.get(name) or ALL[name]()


def clear_overrides() -> None:
    _overrides.clear()
