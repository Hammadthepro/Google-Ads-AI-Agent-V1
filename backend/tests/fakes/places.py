"""TEST FIXTURE ONLY — never imported by the application."""
from __future__ import annotations

from app.adapters.base import NotFoundError
from app.adapters.places import GooglePlacesAdapter
from app.core.util import normalize_term
from tests.fakes.mock_world.failures import EmptyResult, PartialResult, check_failure


class MockGooglePlacesAdapter(GooglePlacesAdapter):
    name = "MockGooglePlacesAdapter"
    source = "MOCK"

    def text_search(self, query: str) -> list[dict]:
        from tests.fakes.mock_world.fixtures import PLACES
        try:
            check_failure("places", "text_search", query)
        except EmptyResult:
            return []
        q = set(normalize_term(query).split())
        scored = []
        for p in PLACES:
            words = set(normalize_term(p["name"]).split())
            score = len(q & words) / max(1, len(words))
            if score >= 0.6:
                scored.append((score, p))
        scored.sort(key=lambda x: -x[0])
        return [{"place_id": p["place_id"], "name": p["name"], "website": p["website"], "simulated": True}
                for _, p in scored]

    def place_details(self, place_id: str) -> dict:
        from tests.fakes.mock_world.fixtures import PLACES
        partial = False
        try:
            check_failure("places", "place_details", place_id)
        except PartialResult:
            partial = True
        for p in PLACES:
            if p["place_id"] == place_id:
                data = dict(p)
                if partial:
                    data.pop("reviews", None)
                    data["partial"] = True
                return data
        raise NotFoundError(f"Place {place_id} not found", adapter=self.name)
