"""Geographic research without Google Places.

OpenStreetMap
  * Nominatim (geocoding) — usage policy: at most 1 request per second, an identifying User-Agent, results cached, attribution
    "© OpenStreetMap contributors" (ODbL). https://operations.osmfoundation.org/policies/nominatim/
  * Overpass (points of interest) — shared public servers with fair-use limits; queries are bounded, throttled and cached.
    The endpoint is configurable so a self-hosted or commercial instance can replace it.
Geoapify (optional, API key) — geocoding and Places search; credit-metered with a free daily allowance and an attribution requirement.

Coverage is not Google's: OpenStreetMap often lacks smaller service businesses, and neither source provides ratings or reviews. Nothing
missing is filled in: a field the source did not return is absent, never guessed.
"""
from __future__ import annotations

import httpx

from app.adapters.base import InvalidResponse, Unsupported
from app.adapters.research.http import classify, json_of, request, throttle

OSM_ATTRIBUTION = "© OpenStreetMap contributors (ODbL 1.0)"
GEOAPIFY_ATTRIBUTION = "Powered by Geoapify · © OpenStreetMap contributors"

# Business type keywords -> OpenStreetMap tags. Only well-established tags; an unmapped type is reported as unsupported.
OSM_TAGS: list[tuple[tuple[str, ...], list[tuple[str, str]]]] = [
    (("plumb",), [("craft", "plumber")]),
    (("electric",), [("craft", "electrician")]),
    (("hvac", "heating", "air condition", "furnace"), [("craft", "hvac")]),
    (("roof",), [("craft", "roofer")]),
    (("dentist", "dental", "orthodont"), [("amenity", "dentist"), ("healthcare", "dentist")]),
    (("lawyer", "attorney", "legal", "law firm"), [("office", "lawyer")]),
    (("veterinar", "vet clinic", "animal hospital"), [("amenity", "veterinary")]),
    (("doctor", "medical", "clinic", "physician"), [("amenity", "doctors"), ("amenity", "clinic")]),
    (("auto repair", "car repair", "mechanic", "garage"), [("shop", "car_repair")]),
    (("locksmith",), [("shop", "locksmith"), ("craft", "locksmith")]),
    (("painter", "painting"), [("craft", "painter")]),
    (("carpent",), [("craft", "carpenter")]),
    (("landscap", "gardener", "lawn"), [("craft", "gardener")]),
    (("real estate", "realtor", "estate agent"), [("office", "estate_agent")]),
    (("insurance",), [("office", "insurance")]),
    (("accountant", "accounting", "bookkeep", "tax prep"), [("office", "accountant")]),
    (("gym", "fitness"), [("leisure", "fitness_centre")]),
    (("hair", "barber", "salon"), [("shop", "hairdresser")]),
    (("beauty", "spa", "nail"), [("shop", "beauty")]),
    (("restaurant",), [("amenity", "restaurant")]),
    (("cafe", "coffee"), [("amenity", "cafe")]),
    (("hotel",), [("tourism", "hotel")]),
    (("pharmac",), [("amenity", "pharmacy")]),
    (("optician", "optometr", "eye care"), [("shop", "optician")]),
    (("physiotherap", "physical therap", "chiropract"), [("healthcare", "physiotherapist"), ("amenity", "doctors")]),
]
GEOAPIFY_CATEGORIES: list[tuple[tuple[str, ...], str]] = [
    (("dentist", "dental"), "healthcare.dentist"),
    (("veterinar", "vet clinic"), "pet.veterinary"),
    (("pharmac",), "healthcare.pharmacy"),
    (("restaurant",), "catering.restaurant"),
    (("cafe", "coffee"), "catering.cafe"),
    (("hotel",), "accommodation.hotel"),
    (("auto repair", "car repair", "mechanic"), "service.vehicle.repair.car"),
    (("hair", "barber", "salon"), "service.beauty.hairdresser"),
    (("gym", "fitness"), "sport.fitness"),
]


def osm_tags_for(text: str) -> list[tuple[str, str]]:
    t = (text or "").lower()
    for keys, tags in OSM_TAGS:
        if any(k in t for k in keys):
            return tags
    return []


def geoapify_category_for(text: str) -> str:
    t = (text or "").lower()
    return next((cat for keys, cat in GEOAPIFY_CATEGORIES if any(k in t for k in keys)), "")


def _poi(name: str, lat, lon, *, source: str, ref: str, category: str, website: str = "", phone: str = "", address: str = "",
         attribution: str) -> dict:
    out = {"name": name, "lat": float(lat), "lon": float(lon), "source": source, "source_ref": ref, "category": category,
           "attribution": attribution}
    for k, v in (("website", website), ("phone", phone), ("address", address)):
        if v:
            out[k] = v
    return out


class OSMAdapter:
    name = "openstreetmap"
    source = "LIVE"

    def __init__(self, client: httpx.Client | None = None, *, nominatim_url: str = "https://nominatim.openstreetmap.org",
                 overpass_url: str = "https://overpass-api.de/api/interpreter", contact_email: str = ""):
        self.client = client or httpx.Client(timeout=40)
        self.nominatim_url = nominatim_url.rstrip("/")
        self.overpass_url = overpass_url
        self.contact_email = contact_email

    def geocode(self, query: str) -> dict | None:
        throttle("nominatim", 1.1)
        params = {"q": query, "format": "jsonv2", "limit": 1, "addressdetails": 1, **({"email": self.contact_email} if self.contact_email else {})}
        resp = request(self.client, "GET", f"{self.nominatim_url}/search", "OpenStreetMap Nominatim", params=params)
        classify(resp, "OpenStreetMap Nominatim")
        data = json_of(resp, "OpenStreetMap Nominatim")
        if not isinstance(data, list):
            raise InvalidResponse("Nominatim answered in an unexpected format", adapter=self.name)
        if not data:
            return None
        d = data[0]
        return {"lat": float(d["lat"]), "lon": float(d["lon"]), "display_name": d.get("display_name", ""), "osm_type": d.get("osm_type"),
                "osm_id": d.get("osm_id"), "type": d.get("type"), "source": "openstreetmap_nominatim", "attribution": OSM_ATTRIBUTION}

    def nearby(self, lat: float, lon: float, radius_m: int, tags: list[tuple[str, str]], *, limit: int = 60) -> list[dict]:
        if not tags:
            raise Unsupported("No OpenStreetMap category is known for this business type", adapter=self.name)
        radius_m = max(500, min(int(radius_m), 50_000))
        parts = "".join(f'nwr["{k}"="{v}"](around:{radius_m},{lat:.5f},{lon:.5f});' for k, v in tags)
        query = f"[out:json][timeout:25];({parts});out center tags {max(1, min(limit, 200))};"
        throttle("overpass", 2.0)
        resp = request(self.client, "POST", self.overpass_url, "OpenStreetMap Overpass", data={"data": query})
        classify(resp, "OpenStreetMap Overpass")
        data = json_of(resp, "OpenStreetMap Overpass")
        if not isinstance(data, dict) or not isinstance(data.get("elements"), list):
            raise InvalidResponse("Overpass answered without an elements list", adapter=self.name)
        out = []
        for el in data["elements"]:
            t = el.get("tags") or {}
            name = t.get("name")
            la, lo = el.get("lat", (el.get("center") or {}).get("lat")), el.get("lon", (el.get("center") or {}).get("lon"))
            if not name or la is None or lo is None:
                continue  # unnamed objects cannot be competitors; nothing is invented
            addr = " ".join(x for x in (t.get("addr:housenumber"), t.get("addr:street"), t.get("addr:city")) if x)
            cat = next((f"{k}={v}" for k, v in tags if t.get(k) == v), "")
            out.append(_poi(name, la, lo, source="openstreetmap_overpass", ref=f"{el.get('type')}/{el.get('id')}", category=cat,
                            website=t.get("website") or t.get("contact:website") or "", phone=t.get("phone") or t.get("contact:phone") or "",
                            address=addr, attribution=OSM_ATTRIBUTION))
        return out


class GeoapifyAdapter:
    name = "geoapify"
    source = "LIVE"
    BASE = "https://api.geoapify.com"

    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self._key = api_key
        self.client = client or httpx.Client(timeout=30)

    def geocode(self, query: str) -> dict | None:
        resp = request(self.client, "GET", f"{self.BASE}/v1/geocode/search", "Geoapify",
                       params={"text": query, "format": "json", "limit": 1, "apiKey": self._key})
        classify(resp, "Geoapify")
        data = json_of(resp, "Geoapify")
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            raise InvalidResponse("Geoapify geocoding answered without a results list", adapter=self.name)
        if not results:
            return None
        r = results[0]
        return {"lat": float(r["lat"]), "lon": float(r["lon"]), "display_name": r.get("formatted", ""), "source": "geoapify_geocoding",
                "attribution": GEOAPIFY_ATTRIBUTION}

    def nearby(self, lat: float, lon: float, radius_m: int, category: str, *, limit: int = 50) -> list[dict]:
        if not category:
            raise Unsupported("No Geoapify category is known for this business type", adapter=self.name)
        radius_m = max(500, min(int(radius_m), 50_000))
        resp = request(self.client, "GET", f"{self.BASE}/v2/places", "Geoapify",
                       params={"categories": category, "filter": f"circle:{lon:.5f},{lat:.5f},{radius_m}", "bias": f"proximity:{lon:.5f},{lat:.5f}",
                               "limit": max(1, min(limit, 100)), "apiKey": self._key})
        classify(resp, "Geoapify")
        data = json_of(resp, "Geoapify")
        feats = data.get("features") if isinstance(data, dict) else None
        if not isinstance(feats, list):
            raise InvalidResponse("Geoapify Places answered without features", adapter=self.name)
        out = []
        for f in feats:
            p = (f or {}).get("properties") or {}
            if not p.get("name") or p.get("lat") is None or p.get("lon") is None:
                continue
            out.append(_poi(p["name"], p["lat"], p["lon"], source="geoapify_places", ref=str(p.get("place_id", "")), category=category,
                            website=p.get("website") or "", phone=((p.get("contact") or {}).get("phone") or ""),
                            address=p.get("formatted") or p.get("address_line2") or "", attribution=GEOAPIFY_ATTRIBUTION))
        return out
