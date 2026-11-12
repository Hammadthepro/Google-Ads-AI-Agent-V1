"""Test doubles for the free research providers so no test touches the public internet.

FakeSuggestions returns deterministic phrases derived from the seed (clearly synthetic: "<seed> near me", "<seed> cost", ...).
FakeOSM geocodes every query to one fixed point and returns the POIs a test puts in `pois` (none by default)."""
from __future__ import annotations

from app.adapters.base import Unsupported


class FakeSuggestions:
    name = "search_suggestions"
    source = "MOCK"
    enabled = ("google", "duckduckgo")

    def __init__(self):
        self.fail: dict[str, Exception] = {}
        self.calls: list[tuple[str, str]] = []

    def suggest(self, source: str, query: str, *, hl: str = "en", gl: str = "us") -> list[str]:
        self.calls.append((source, query))
        if source in self.fail:
            raise self.fail[source]
        if source == "google":
            return [f"{query} near me", f"{query} cost", f"best {query}"]
        return [f"{query} near me", f"{query} services"]


class FakeOSM:
    name = "openstreetmap"
    source = "MOCK"

    def __init__(self):
        self.pois: list[dict] = []
        self.fail_nearby: Exception | None = None

    def geocode(self, query: str) -> dict | None:
        return {"lat": 30.2672, "lon": -97.7431, "display_name": f"{query} (test location)", "source": "openstreetmap_nominatim",
                "attribution": "© OpenStreetMap contributors (ODbL 1.0)"}

    def nearby(self, lat, lon, radius_m, tags, *, limit=60):
        if self.fail_nearby:
            raise self.fail_nearby
        if not tags:
            raise Unsupported("No OpenStreetMap category is known for this business type", adapter=self.name)
        return list(self.pois)
