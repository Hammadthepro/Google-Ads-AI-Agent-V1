"""KeywordResearchAdapter interface + real (Google Ads KeywordPlanIdeaService)."""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.adapters.base import AdapterError, InvalidInput
from app.core import clock

def _present(m, name: str):
    """The metric if Google actually returned it, else None. Protobuf ints default to 0 when absent, which would read as 'zero searches' /
    'free clicks', so use field presence (optional fields) and fall back to 'None when falsy' for fields without presence."""
    try:
        if not type(m).pb(m).HasField(name):
            return None
    except (ValueError, AttributeError, TypeError):
        pass
    v = getattr(m, name, None)
    return v if v not in (None, "") else None


def idea_row(text: str, m, *, retrieved_at: str, location: list[str], language: str, network: str, seeds: list[str], page_url: str = "") -> dict:
    """One keyword idea with its provenance. Missing metrics are None and listed in ``metrics_unavailable``; nothing is zero-filled."""
    comp = getattr(m.competition, "name", str(m.competition))
    comp = None if comp in ("UNSPECIFIED", "UNKNOWN", "0", "") else comp
    row = {"keyword": text, "avg_monthly_searches": _present(m, "avg_monthly_searches"), "competition": comp,
           "competition_index": _present(m, "competition_index"),
           "low_top_of_page_bid_micros": _present(m, "low_top_of_page_bid_micros"),
           "high_top_of_page_bid_micros": _present(m, "high_top_of_page_bid_micros"),
           "source": "LIVE", "provider": "google_keyword_planner", "retrieved_at": retrieved_at, "location": location, "language": language,
           "network": network, "seed_keywords": seeds, "seed_url": page_url}
    row["metrics_unavailable"] = [k for k in ("avg_monthly_searches", "competition", "competition_index", "low_top_of_page_bid_micros",
                                              "high_top_of_page_bid_micros") if row[k] is None]
    return row


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

    def _customer(self, deployment_customer_id: str = "") -> str:
        """The RESEARCH customer. The deployment customer passed by callers is deliberately ignored: research never runs under, or
        depends on, a client's account, and no discovered account is ever borrowed. Missing configuration is reported exactly."""
        from app.services import integrations_service
        vals = integrations_service.ensure_configured("keyword_planner")  # raises IntegrationNotConfigured(missing=research_customer_id)
        cid = (vals.get("research_customer_id") or "").replace("-", "").strip()
        if not (cid.isdigit() and len(cid) == 10):
            raise InvalidInput("The Keyword Planner research customer ID must be a 10-digit Google Ads customer ID.", adapter=self.name)
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
            retrieved = clock.now().isoformat()
            location = list(req.geo_target_constants)
            for idea in svc.generate_keyword_ideas(request=req):
                out.append(idea_row(idea.text, idea.keyword_idea_metrics, retrieved_at=retrieved, location=location,
                                    language=req.language, network="GOOGLE_SEARCH", seeds=list(seeds), page_url=page_url))
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
