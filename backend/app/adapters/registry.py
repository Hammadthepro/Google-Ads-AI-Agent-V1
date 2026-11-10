"""The ONLY place that builds adapters. Every adapter here is REAL.

There is no mock selection and no fallback: when an integration's credentials are missing (or it was disabled on the
Integrations page) the getter raises IntegrationNotConfigured, which the API turns into a clear "configure it on the
Integrations page" message. Nothing silently switches to another provider or to simulated data.

Tests install isolated fakes with `override()` (see backend/tests/fakes); the application never imports them.
"""
from __future__ import annotations

from functools import lru_cache

from app.services import integrations_service


@lru_cache
def google_ads():
    integrations_service.ensure_configured("google_ads")
    from app.adapters.google_ads.real import RealGoogleAdsAdapter
    return RealGoogleAdsAdapter()


@lru_cache
def keyword_research():
    from app.adapters.keyword_research import GoogleKeywordPlannerAdapter
    return GoogleKeywordPlannerAdapter(google_ads())


@lru_cache
def places():
    integrations_service.ensure_configured("places")
    from app.adapters.places import RealGooglePlacesAdapter
    return RealGooglePlacesAdapter()


@lru_cache
def pagespeed():
    integrations_service.ensure_configured("pagespeed")
    from app.adapters.pagespeed import RealPageSpeedAdapter
    return RealPageSpeedAdapter()


@lru_cache
def crawler():
    """Live HTTP fetching with SSRF protection. It needs no credentials, so it is always the real crawler."""
    from app.adapters.crawler import LiveCrawlerAdapter
    return LiveCrawlerAdapter()


@lru_cache
def ai():
    """The internal AI (Groq). No Groq key/model -> IntegrationNotConfigured; never a stand-in model."""
    integrations_service.ensure_configured("groq")
    from app.adapters.ai import GroqAIProvider
    return GroqAIProvider()


@lru_cache
def claude_ai():
    """The independent second AI (Claude). Not configured -> IntegrationNotConfigured."""
    v = integrations_service.ensure_configured("claude")
    from app.adapters.ai.claude import ClaudeAIProvider
    return ClaudeAIProvider(v.get("api_key", ""), v.get("model", ""))


@lru_cache
def auth():
    from app.adapters.auth import DevLoginAuthenticationProvider, GoogleOIDCAuthenticationProvider
    from app.core.config import get_settings
    return GoogleOIDCAuthenticationProvider() if get_settings().AUTH_PROVIDER == "google" else DevLoginAuthenticationProvider()


@lru_cache
def notifier():
    """Email via SMTP when SMTP is configured; otherwise alerts are kept in-app only and recorded as NOT_EMAILED."""
    from app.adapters.notifications import EmailNotifier, InAppNotifier
    v = integrations_service.resolve("smtp")
    return EmailNotifier() if v.get("host") and v.get("from_email") and not v.get("_disabled") else InAppNotifier()


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
