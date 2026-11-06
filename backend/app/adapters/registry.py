"""The ONLY place that chooses mock vs real implementations.

Real implementations are chosen only by explicit *_MODE / provider settings — never because credentials exist.
In real mode, missing credentials raise at first use (no silent fallback to mock).
"""
from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings


@lru_cache
def google_ads():
    from app.adapters.google_ads.mock import MockGoogleAdsAdapter
    from app.adapters.google_ads.real import RealGoogleAdsAdapter
    return RealGoogleAdsAdapter() if get_settings().mode_for("GOOGLE_ADS") == "real" else MockGoogleAdsAdapter()


@lru_cache
def keyword_research():
    from app.adapters.keyword_research import GoogleKeywordPlannerAdapter, MockKeywordResearchAdapter
    if get_settings().mode_for("KEYWORD_RESEARCH") == "real":
        return GoogleKeywordPlannerAdapter(google_ads())
    return MockKeywordResearchAdapter()


@lru_cache
def places():
    from app.adapters.places import MockGooglePlacesAdapter, RealGooglePlacesAdapter
    return RealGooglePlacesAdapter() if get_settings().mode_for("PLACES") == "real" else MockGooglePlacesAdapter()


@lru_cache
def pagespeed():
    from app.adapters.pagespeed import MockPageSpeedAdapter, RealPageSpeedAdapter
    return RealPageSpeedAdapter() if get_settings().mode_for("PAGESPEED") == "real" else MockPageSpeedAdapter()


@lru_cache
def crawler():
    from app.adapters.crawler import FixtureCrawlerAdapter, LiveCrawlerAdapter
    return LiveCrawlerAdapter() if get_settings().CRAWLER_MODE == "live" else FixtureCrawlerAdapter()


@lru_cache
def ai():
    from app.adapters.ai import GroqAIProvider, MockAIProvider
    s = get_settings()
    return GroqAIProvider() if (s.AI_PROVIDER == "groq" and s.GROQ_ENABLED) else MockAIProvider()


@lru_cache
def auth():
    from app.adapters.auth import GoogleOIDCAuthenticationProvider, MockAuthenticationProvider
    return GoogleOIDCAuthenticationProvider() if get_settings().AUTH_PROVIDER == "google" else MockAuthenticationProvider()


@lru_cache
def ads_oauth():
    from app.adapters.ads_oauth import GoogleAdsOAuthAdapter, MockAdsOAuthAdapter
    return GoogleAdsOAuthAdapter() if get_settings().mode_for("GOOGLE_ADS") == "real" else MockAdsOAuthAdapter()


@lru_cache
def notifier():
    from app.adapters.notifications import EmailNotifier, MockNotifier
    return EmailNotifier() if get_settings().mode_for("NOTIFIER") == "real" else MockNotifier()


ALL = {"google_ads": google_ads, "keyword_research": keyword_research, "places": places, "pagespeed": pagespeed,
       "crawler": crawler, "ai": ai, "auth": auth, "ads_oauth": ads_oauth, "notifier": notifier}


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
