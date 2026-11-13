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
    ads = google_ads()  # the Google Ads authorisation comes first
    integrations_service.ensure_configured("keyword_planner")  # then the research customer ID; no client account is ever borrowed
    from app.adapters.keyword_research import GoogleKeywordPlannerAdapter
    return GoogleKeywordPlannerAdapter(ads)


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
    """The Main AI: the provider and model the workspace chose for its Main AI role (an existing Groq setup is adopted as the Main AI).
    Not configured or turned off -> IntegrationNotConfigured; never a stand-in model, never another provider."""
    from app.services import ai_roles
    return ai_roles.build_provider("main")


@lru_cache
def challenger_ai():
    """The Challenger AI: reviews important Main AI outputs. Optional; not configured / off -> IntegrationNotConfigured, which callers
    record as "independent review not performed"."""
    from app.services import ai_roles
    return ai_roles.build_provider("challenger")


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


@lru_cache
def tavily():
    vals = integrations_service.ensure_configured("tavily")
    from app.adapters.research.tavily import TavilyAdapter
    return TavilyAdapter(vals["api_key"], depth=vals.get("search_depth", "basic"))


@lru_cache
def brave_search():
    vals = integrations_service.ensure_configured("brave_search")
    from app.adapters.research.brave import BraveSearchAdapter
    return BraveSearchAdapter(vals["api_key"])


@lru_cache
def search_suggestions():
    vals = integrations_service.ensure_configured("search_suggestions")  # needs no credentials; raises only when switched off
    from app.adapters.research.suggest import SuggestionAdapter
    return SuggestionAdapter(enabled=tuple(x.strip() for x in (vals.get("sources") or "").split(",") if x.strip()))


@lru_cache
def openstreetmap():
    vals = integrations_service.ensure_configured("openstreetmap")
    from app.adapters.research.geo import OSMAdapter
    return OSMAdapter(nominatim_url=vals.get("nominatim_url") or "https://nominatim.openstreetmap.org",
                      overpass_url=vals.get("overpass_url") or "https://overpass-api.de/api/interpreter", contact_email=vals.get("contact_email", ""))


@lru_cache
def geoapify():
    vals = integrations_service.ensure_configured("geoapify")
    from app.adapters.research.geo import GeoapifyAdapter
    return GeoapifyAdapter(vals["api_key"])


ALL = {"google_ads": google_ads, "keyword_research": keyword_research, "places": places, "pagespeed": pagespeed,
       "crawler": crawler, "ai": ai, "challenger_ai": challenger_ai, "auth": auth, "notifier": notifier,
       "tavily": tavily, "brave_search": brave_search, "search_suggestions": search_suggestions, "openstreetmap": openstreetmap, "geoapify": geoapify}


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


def optional(name: str):
    """The adapter, or None when its integration is not configured. For steps that only ANNOTATE (data source labels) or that can be
    skipped honestly (Google's validate-only call, duplicate-name lookups); never for reading data a decision depends on."""
    from app.core.errors import IntegrationNotConfigured
    try:
        return get(name)
    except IntegrationNotConfigured:
        return None


def clear_overrides() -> None:
    _overrides.clear()
