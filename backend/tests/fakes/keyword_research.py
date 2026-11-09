"""TEST FIXTURE ONLY — never imported by the application."""
from __future__ import annotations

import hashlib

from app.adapters.keyword_research import KeywordResearchAdapter
from tests.fakes.mock_world.failures import EmptyResult, check_failure

IDEA_PATTERNS = [
    ("{s}", 1.0, 1.0), ("{s} near me", 0.9, 1.1), ("{s} {city}", 0.35, 1.0), ("emergency {s}", 0.25, 1.4),
    ("{s} cost", 0.3, 0.8), ("best {s}", 0.2, 1.0), ("{s} company", 0.15, 1.0), ("affordable {s}", 0.12, 0.8),
    ("cheap {s}", 0.1, 0.7), ("{s} jobs", 0.18, 0.3), ("diy {s}", 0.14, 0.2), ("how to {s}", 0.22, 0.2),
    ("free {s}", 0.08, 0.3), ("{s} salary", 0.05, 0.2), ("{s} reviews", 0.1, 0.9),
]
GENERIC = {"home_services": ["plumber near me", "plumbing company", "hvac company near me"],
           "dental": ["dentist near me", "dental office near me"],
           "legal": ["lawyer near me", "attorney near me"]}


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
