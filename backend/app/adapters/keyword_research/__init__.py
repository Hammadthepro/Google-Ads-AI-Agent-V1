"""KeywordResearchAdapter interface + mock + real (Google Ads KeywordPlanIdeaService)."""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod

from app.adapters.base import AdapterError
from app.mock_world.failures import EmptyResult, check_failure

IDEA_PATTERNS = [
    ("{s}", 1.0, 1.0), ("{s} near me", 0.9, 1.1), ("{s} {city}", 0.35, 1.0), ("emergency {s}", 0.25, 1.4),
    ("{s} cost", 0.3, 0.8), ("best {s}", 0.2, 1.0), ("{s} company", 0.15, 1.0), ("affordable {s}", 0.12, 0.8),
    ("cheap {s}", 0.1, 0.7), ("{s} jobs", 0.18, 0.3), ("diy {s}", 0.14, 0.2), ("how to {s}", 0.22, 0.2),
    ("free {s}", 0.08, 0.3), ("{s} salary", 0.05, 0.2), ("{s} reviews", 0.1, 0.9),
]
GENERIC = {"home_services": ["plumber near me", "plumbing company", "hvac company near me"],
           "dental": ["dentist near me", "dental office near me"],
           "legal": ["lawyer near me", "attorney near me"]}


class KeywordResearchAdapter(ABC):
    name = "keyword_research"
    source = "MOCK"

    @abstractmethod
    def generate_ideas(self, customer_id: str, seeds: list[str], *, page_url: str = "", location_ids: list[str] | None = None,
                       language: str = "en", city: str = "") -> list[dict]:
        """Return [{keyword, avg_monthly_searches, competition, low_top_of_page_bid_micros, high_top_of_page_bid_micros}]."""

    @abstractmethod
    def suggest_geo_targets(self, zip_codes: list[str], country: str = "US") -> list[dict]: ...


def _h(*parts) -> int:
    return int(hashlib.md5("|".join(parts).encode()).hexdigest()[:8], 16)


class MockKeywordResearchAdapter(KeywordResearchAdapter):
    name = "MockKeywordResearchAdapter"
    source = "MOCK"

    def generate_ideas(self, customer_id, seeds, *, page_url="", location_ids=None, language="en", city=""):
        try:
            check_failure("keyword_research", "generate_ideas", ",".join(seeds))
        except EmptyResult:
            return []
        out, seen = [], set()
        for seed in seeds:
            s = seed.lower().strip()
            base_vol = 300 + _h(s) % 2200
            base_bid = 2.5 + (_h(s, "bid") % 1800) / 100
            for pat, vol_mult, bid_mult in IDEA_PATTERNS:
                kw = pat.format(s=s, city=(city or "local").lower()).strip()
                if kw in seen:
                    continue
                seen.add(kw)
                vol = int(base_vol * vol_mult * (0.8 + (_h(kw) % 40) / 100))
                low = base_bid * bid_mult * 0.6
                comp_idx = _h(kw, "c") % 100
                out.append({"keyword": kw, "avg_monthly_searches": max(10, round(vol, -1)),
                            "competition": "HIGH" if comp_idx > 66 else "MEDIUM" if comp_idx > 33 else "LOW",
                            "competition_index": comp_idx,
                            "low_top_of_page_bid_micros": int(low * 1_000_000),
                            "high_top_of_page_bid_micros": int(low * 2.1 * 1_000_000),
                            "seed": seed, "source": "MOCK"})
        return out

    def suggest_geo_targets(self, zip_codes, country="US"):
        return [{"zip": z, "geo_target_constant": f"geoTargetConstants/9{_h(z) % 100000:05d}", "simulated": True}
                for z in zip_codes]


class GoogleKeywordPlannerAdapter(KeywordResearchAdapter):
    """KeywordPlanIdeaService.GenerateKeywordIdeas via the official client. Not verified against a live account."""

    name = "GoogleKeywordPlannerAdapter"
    source = "LIVE"

    def __init__(self, ads_adapter):
        self.ads = ads_adapter  # RealGoogleAdsAdapter (shares the client)

    def generate_ideas(self, customer_id, seeds, *, page_url="", location_ids=None, language="en", city=""):
        def run():
            client = self.ads.client()
            svc = client.get_service("KeywordPlanIdeaService")
            req = client.get_type("GenerateKeywordIdeasRequest")
            req.customer_id = customer_id.replace("-", "")
            req.language = "languageConstants/1000"  # English
            req.geo_target_constants.extend(location_ids or ["geoTargetConstants/2840"])  # United States
            req.keyword_plan_network = client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH
            if seeds and page_url:
                req.keyword_and_url_seed.url = page_url
                req.keyword_and_url_seed.keywords.extend(seeds)
            elif page_url:
                req.url_seed.url = page_url
            else:
                req.keyword_seed.keywords.extend(seeds)
            out = []
            for idea in svc.generate_keyword_ideas(request=req):
                m = idea.keyword_idea_metrics
                out.append({"keyword": idea.text, "avg_monthly_searches": m.avg_monthly_searches,
                            "competition": m.competition.name if hasattr(m.competition, "name") else str(m.competition),
                            "competition_index": m.competition_index,
                            "low_top_of_page_bid_micros": m.low_top_of_page_bid_micros,
                            "high_top_of_page_bid_micros": m.high_top_of_page_bid_micros, "source": "LIVE"})
            return out
        return self.ads._wrap(run)

    def suggest_geo_targets(self, zip_codes, country="US"):
        raise AdapterError("GeoTargetConstantService lookup not yet verified for the real adapter", adapter=self.name)
