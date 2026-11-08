"""GooglePlacesAdapter interface + MockGooglePlacesAdapter + RealGooglePlacesAdapter (Places API New)."""
from __future__ import annotations

from abc import ABC, abstractmethod

import httpx

from app.adapters.base import AdapterTimeout, AuthError, NotFoundError, RateLimited, ServiceUnavailable
from app.core.config import get_settings
from app.core.util import normalize_term
from app.mock_world.failures import EmptyResult, PartialResult, check_failure


class GooglePlacesAdapter(ABC):
    name = "places"
    source = "MOCK"

    @abstractmethod
    def text_search(self, query: str) -> list[dict]: ...

    @abstractmethod
    def place_details(self, place_id: str) -> dict: ...

    def find_business(self, name: str, website: str = "", address: str = "") -> dict | None:
        results = self.text_search(f"{name} {address}".strip())
        host = website.split("//")[-1].split("/")[0].lower() if website else ""
        for r in results:
            if host and host in (r.get("website") or ""):
                return self.place_details(r["place_id"])
        return self.place_details(results[0]["place_id"]) if results else None


class MockGooglePlacesAdapter(GooglePlacesAdapter):
    name = "MockGooglePlacesAdapter"
    source = "MOCK"

    def text_search(self, query: str) -> list[dict]:
        from app.mock_world.fixtures import PLACES
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
        from app.mock_world.fixtures import PLACES
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


class RealGooglePlacesAdapter(GooglePlacesAdapter):
    """Places API (New). Not verified against a live key during development."""

    name = "RealGooglePlacesAdapter"
    source = "LIVE"
    BASE = "https://places.googleapis.com/v1"
    FIELDS = ("id,displayName,formattedAddress,nationalPhoneNumber,websiteUri,rating,userRatingCount,"
              "primaryTypeDisplayName,regularOpeningHours,photos,reviews,businessStatus")

    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=15)

    def _headers(self, mask: str) -> dict:
        from app.services import integrations_service
        key = integrations_service.resolve("places").get("api_key", "")
        if not key:
            raise AuthError("GOOGLE_PLACES_API_KEY is not configured", adapter=self.name)
        return {"X-Goog-Api-Key": key, "X-Goog-FieldMask": mask}

    def _check(self, resp: httpx.Response):
        if resp.status_code in (401, 403):
            raise AuthError(f"Places API auth error {resp.status_code}", adapter=self.name)
        if resp.status_code == 429:
            raise RateLimited("Places API rate limited", adapter=self.name)
        if resp.status_code == 404:
            raise NotFoundError("Place not found", adapter=self.name)
        if resp.status_code >= 500:
            raise ServiceUnavailable(f"Places API {resp.status_code}", adapter=self.name)
        resp.raise_for_status()
        return resp.json()

    def text_search(self, query: str) -> list[dict]:
        try:
            data = self._check(self.client.post(f"{self.BASE}/places:searchText", json={"textQuery": query},
                                                headers=self._headers("places.id,places.displayName,places.websiteUri")))
        except httpx.TimeoutException as exc:
            raise AdapterTimeout(str(exc), adapter=self.name) from exc
        return [{"place_id": p["id"], "name": p.get("displayName", {}).get("text", ""), "website": p.get("websiteUri", "")}
                for p in data.get("places", [])]

    def place_details(self, place_id: str) -> dict:
        try:
            p = self._check(self.client.get(f"{self.BASE}/places/{place_id}", headers=self._headers(self.FIELDS)))
        except httpx.TimeoutException as exc:
            raise AdapterTimeout(str(exc), adapter=self.name) from exc
        return {"place_id": p.get("id"), "name": p.get("displayName", {}).get("text", ""),
                "website": p.get("websiteUri", ""), "phone": p.get("nationalPhoneNumber", ""),
                "address": p.get("formattedAddress", ""),
                "primary_category": p.get("primaryTypeDisplayName", {}).get("text", ""),
                "rating": p.get("rating"), "user_rating_count": p.get("userRatingCount", 0),
                "photo_count": len(p.get("photos", [])),  # API returns a sample, not the full count
                "opening_hours": {"weekday_text": p.get("regularOpeningHours", {}).get("weekdayDescriptions", [])},
                "business_status": p.get("businessStatus", ""),
                "reviews": [{"author": r.get("authorAttribution", {}).get("displayName", ""), "rating": r.get("rating"),
                             "text": r.get("text", {}).get("text", ""), "relative_time": r.get("relativePublishTimeDescription", ""),
                             "simulated": False} for r in p.get("reviews", [])],
                "simulated": False}
