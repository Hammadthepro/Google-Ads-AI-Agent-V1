"""PageSpeedAdapter interface + mock + real (PageSpeed Insights v5 runPagespeed)."""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod

import httpx

from app.adapters.base import AdapterTimeout, NotFoundError, RateLimited, ServiceUnavailable
from app.mock_world.failures import check_failure


class PageSpeedAdapter(ABC):
    name = "pagespeed"
    source = "MOCK"

    @abstractmethod
    def run(self, url: str, strategy: str) -> dict:
        """strategy: mobile | desktop. Returns performance_score 0-100, core web vitals, opportunities."""


def _opportunities(score: int) -> list[dict]:
    opps = []
    if score < 90:
        opps.append({"id": "render-blocking-resources", "title": "Eliminate render-blocking resources", "savings_ms": (90 - score) * 18})
    if score < 75:
        opps.append({"id": "uses-optimized-images", "title": "Efficiently encode and resize images", "savings_ms": (75 - score) * 30})
    if score < 60:
        opps.append({"id": "unused-javascript", "title": "Reduce unused JavaScript (tag/widget bloat)", "savings_ms": (60 - score) * 40})
    return opps


class MockPageSpeedAdapter(PageSpeedAdapter):
    name = "MockPageSpeedAdapter"
    source = "MOCK"

    def run(self, url: str, strategy: str) -> dict:
        from app.mock_world.fixtures import PAGESPEED
        check_failure("pagespeed", "run", url)
        host = url.split("//")[-1].split("/")[0].lower()
        if host in PAGESPEED:
            base = PAGESPEED[host][strategy]
        else:
            h = int(hashlib.md5(f"{host}{strategy}".encode()).hexdigest()[:4], 16)
            base = (45 + h % 40) if strategy == "mobile" else (70 + h % 25)
        path_adj = int(hashlib.md5(url.encode()).hexdigest()[:2], 16) % 7 - 3
        score = max(5, min(100, base + path_adj))
        lcp = round(1.2 + (100 - score) * 0.06, 2)
        return {"url": url, "strategy": strategy, "performance_score": score,
                "core_web_vitals": {"lcp_seconds": lcp, "cls": round(0.02 + (100 - score) * 0.002, 3),
                                    "inp_ms": int(120 + (100 - score) * 4)},
                "opportunities": _opportunities(score), "source": "MOCK"}


class RealPageSpeedAdapter(PageSpeedAdapter):
    """Not verified against a live key during development."""

    name = "RealPageSpeedAdapter"
    source = "LIVE"
    ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=90)

    def run(self, url: str, strategy: str) -> dict:
        params = {"url": url, "strategy": strategy.upper(), "category": "PERFORMANCE"}
        from app.services import integrations_service
        key = integrations_service.resolve("pagespeed").get("api_key")
        if key:
            params["key"] = key
        try:
            resp = self.client.get(self.ENDPOINT, params=params)
        except httpx.TimeoutException as exc:
            raise AdapterTimeout(str(exc), adapter=self.name) from exc
        if resp.status_code == 429:
            raise RateLimited("PageSpeed quota exceeded", adapter=self.name)
        if resp.status_code >= 500:
            raise ServiceUnavailable(f"PageSpeed {resp.status_code}", adapter=self.name)
        if resp.status_code == 400:
            raise NotFoundError("PageSpeed could not analyse the URL", adapter=self.name)
        resp.raise_for_status()
        lh = resp.json().get("lighthouseResult", {})
        audits = lh.get("audits", {})
        score = int(round((lh.get("categories", {}).get("performance", {}).get("score") or 0) * 100))
        opps = [{"id": k, "title": v.get("title"), "savings_ms": v.get("details", {}).get("overallSavingsMs", 0)}
                for k, v in audits.items() if v.get("details", {}).get("type") == "opportunity"
                and (v.get("details", {}).get("overallSavingsMs") or 0) > 0]
        return {"url": url, "strategy": strategy, "performance_score": score,
                "core_web_vitals": {"lcp_seconds": round((audits.get("largest-contentful-paint", {}).get("numericValue") or 0) / 1000, 2),
                                    "cls": audits.get("cumulative-layout-shift", {}).get("numericValue"),
                                    "inp_ms": audits.get("interaction-to-next-paint", {}).get("numericValue")},
                "opportunities": opps, "source": "LIVE"}
