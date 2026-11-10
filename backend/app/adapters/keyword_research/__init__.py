"""KeywordResearchAdapter interface + real (Google Ads KeywordPlanIdeaService)."""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.adapters.base import AdapterError, InvalidInput

class KeywordResearchAdapter(ABC):
    name = "keyword_research"
    source = "LIVE"

    @abstractmethod
    def generate_ideas(self, customer_id: str, seeds: list[str], *, page_url: str = "", location_ids: list[str] | None = None,
                       language: str = "en", city: str = "") -> list[dict]:
        """Return [{keyword, avg_monthly_searches, competition, low_top_of_page_bid_micros, high_top_of_page_bid_micros}]."""

    @abstractmethod
    def suggest_geo_targets(self, zip_codes: list[str], country: str = "US") -> list[dict]: ...






class GoogleKeywordPlannerAdapter(KeywordResearchAdapter):
    """KeywordPlanIdeaService.GenerateKeywordIdeas via the official client (read-only; shares the Google Ads connection)."""

    name = "GoogleKeywordPlannerAdapter"
    source = "LIVE"

    def __init__(self, ads_adapter):
        self.ads = ads_adapter  # RealGoogleAdsAdapter (shares the client)

    def _customer(self, customer_id: str) -> str:
        """Keyword ideas do not depend on one client's data, so a client without a linked account uses the connection's
        configured client account. With neither, fail clearly BEFORE calling Google (an empty ID is a guaranteed API error)."""
        from app.db.session import session_scope
        from app.services import mcc_service
        cid = (customer_id or "").replace("-", "").strip()
        if not cid:
            with session_scope() as db:
                cid = mcc_service.any_client_account(db)
        if not cid.isdigit():
            raise InvalidInput("Keyword Planner needs a Google Ads client account. Discover your accounts first (Integrations > Google Ads "
                               "> choose a manager > refresh accounts).", adapter=self.name)
        return cid

    def generate_ideas(self, customer_id, seeds, *, page_url="", location_ids=None, language="en", city=""):
        cid = self._customer(customer_id)

        def run():
            client = self.ads.client(None if self.ads._factory else self.ads._login_for(cid))
            svc = client.get_service("KeywordPlanIdeaService")
            req = client.get_type("GenerateKeywordIdeasRequest")
            req.customer_id = cid
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
        return self.ads._read(run)

    def suggest_geo_targets(self, zip_codes, country="US"):
        """GeoTargetConstantService.SuggestGeoTargetConstants — ZIP codes -> Google location IDs (postal codes preferred)."""
        def run():
            client = self.ads.client(None)
            svc = client.get_service("GeoTargetConstantService")
            req = client.get_type("SuggestGeoTargetConstantsRequest")
            req.locale = "en"
            req.country_code = country
            req.location_names.names.extend(zip_codes)
            resp = svc.suggest_geo_target_constants(request=req)
            best: dict[str, dict] = {}
            for sug in resp.geo_target_constant_suggestions:
                g = sug.geo_target_constant
                term = sug.search_term or ""
                row = {"zip": term, "geo_target_constant": g.resource_name, "name": g.canonical_name,
                       "target_type": g.target_type, "simulated": False}
                if term not in best or (g.target_type == "Postal Code" and best[term]["target_type"] != "Postal Code"):
                    best[term] = row
            missing = [z for z in zip_codes if z not in best]
            if missing:
                raise AdapterError(f"No Google location found for: {', '.join(missing)}", adapter=self.name)
            return [best[z] for z in zip_codes]
        return self.ads._wrap(run)
