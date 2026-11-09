"""TEST FIXTURE ONLY — never imported by the application."""
from __future__ import annotations

import hashlib

from app.adapters.pagespeed import PageSpeedAdapter, _opportunities
from tests.fakes.mock_world.failures import check_failure


class MockPageSpeedAdapter(PageSpeedAdapter):
    name = "MockPageSpeedAdapter"
    source = "MOCK"

    def run(self, url: str, strategy: str) -> dict:
        from tests.fakes.mock_world.fixtures import PAGESPEED
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
