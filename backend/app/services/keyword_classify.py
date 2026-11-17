"""Structured keyword classification: one deterministic class, intent, location relevance, commercial relevance and selection status per
keyword, each with the reason. Rules only (no AI score stands in for a metric) and no data is invented: a keyword nobody measured is
`unmeasured`, never given a number.

Classes (CLASSES): high-intent commercial, local service, transactional, informational, DIY / educational, job-seeking, irrelevant
service, irrelevant location, competitor / navigation, unmeasured. Selection: `eligible` (may enter the campaign), `research_only` (kept
as evidence, not targeted) or `rejected` (with the reason)."""
from __future__ import annotations

import re

from app.core.util import normalize_term

CLASSES = {
    "high_intent_commercial": "High-intent commercial", "local_service": "Local service", "transactional": "Transactional",
    "informational": "Informational", "diy_educational": "DIY / educational", "job_seeking": "Job-seeking",
    "irrelevant_service": "Irrelevant service", "irrelevant_location": "Irrelevant location", "competitor_navigation": "Competitor / navigation",
    "unmeasured": "Unmeasured",
}
REJECTED_CLASSES = ("job_seeking", "irrelevant_service", "irrelevant_location", "competitor_navigation", "diy_educational")

JOB = ("job", "jobs", "career", "careers", "hiring", "salary", "salaries", "wage", "wages", "employment", "apprentice", "apprenticeship",
       "internship", "resume", "indeed", "glassdoor", "work from home", "technician jobs", "school", "course", "courses", "training",
       "certification", "license exam", "how to become")
DIY = ("diy", "how to", "how do i", "yourself", "tutorial", "youtube", "video", "videos", "step by step", "home remedy", "hack", "hacks",
       "kit", "kits", "parts", "manual", "pdf")
INFO = ("what is", "what are", "why", "meaning", "definition", "ideas", "examples", "vs", "versus", "types of", "history", "wiki",
        "reddit", "statistics", "facts")
HIGH_INTENT = ("emergency", "same day", "24 hour", "24/7", "near me", "quote", "quotes", "estimate", "estimates", "cost", "price", "prices",
               "pricing", "hire", "book", "appointment", "company", "companies", "contractor", "contractors", "service", "services",
               "repair", "installation", "install", "replacement", "best", "top rated", "licensed", "affordable", "cheap", "local")
TRANSACT = ("buy", "order", "book", "schedule", "hire", "quote", "quotes", "estimate", "appointment", "call", "near me", "emergency")
NAV_PLATFORMS = ("yelp", "angi", "angie", "angies", "homeadvisor", "thumbtack", "craigslist", "facebook", "nextdoor", "bbb", "google",
                 "amazon", "home depot", "lowes", "taskrabbit", "porch")

US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO", "connecticut": "CT",
    "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH",
    "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
# Two-letter abbreviations that are also common English words are not treated as places ("in", "or", "me", "hi", "ok", "oh", "pa" ...).
SAFE_ABBR = {"nj", "ny", "nyc", "tx", "fl", "ca", "az", "nv", "nc", "sc", "va", "wv", "md", "ct", "ri", "nh", "vt", "ga", "tn", "ky", "mn",
             "wi", "ia", "ks", "ne", "nm", "ut", "wy", "mt", "nd", "sd", "ms", "mo", "ar", "la", "dc"}
MAJOR_CITIES = (
    "new york city", "los angeles", "chicago", "houston", "phoenix", "philadelphia", "san antonio", "san diego", "dallas", "san jose",
    "austin", "jacksonville", "fort worth", "columbus", "charlotte", "san francisco", "indianapolis", "seattle", "denver", "boston",
    "el paso", "nashville", "detroit", "oklahoma city", "portland", "las vegas", "memphis", "louisville", "baltimore", "milwaukee",
    "albuquerque", "tucson", "fresno", "sacramento", "kansas city", "mesa", "atlanta", "omaha", "colorado springs", "raleigh", "miami",
    "long beach", "virginia beach", "oakland", "minneapolis", "tulsa", "tampa", "arlington", "new orleans", "wichita", "cleveland",
    "bakersfield", "anaheim", "honolulu", "santa ana", "riverside", "corpus christi", "lexington", "pittsburgh", "anchorage",
    "stockton", "cincinnati", "st louis", "saint louis", "st paul", "toledo", "greensboro", "newark", "plano", "henderson",
    "buffalo", "jersey city", "fort wayne", "orlando", "st petersburg", "chandler", "laredo", "norfolk", "durham", "madison", "lubbock",
    "irvine", "winston salem", "glendale", "garland", "hialeah", "reno", "chesapeake", "gilbert", "baton rouge", "irving", "scottsdale",
    "north las vegas", "fremont", "boise", "richmond", "san bernardino", "birmingham", "spokane", "rochester", "des moines", "modesto",
    "fayetteville", "tacoma", "oxnard", "fontana", "montgomery", "moreno valley", "shreveport", "yonkers", "akron", "huntington beach",
    "little rock", "augusta", "amarillo", "grand rapids", "salt lake city", "tallahassee", "huntsville", "grand prairie",
    "knoxville", "worcester", "providence", "brooklyn", "queens", "bronx", "manhattan", "staten island", "harrisburg", "allentown",
    "scranton", "erie", "lancaster", "york", "hartford", "new haven", "stamford", "trenton", "camden", "wilmington", "dover",
)
COUNTRIES = (
    "canada", "mexico", "uk", "united kingdom", "england", "london", "scotland", "ireland", "australia", "sydney", "melbourne", "india",
    "delhi", "mumbai", "bangalore", "pakistan", "karachi", "lahore", "dubai", "uae", "singapore", "philippines", "manila", "nigeria",
    "lagos", "kenya", "south africa", "germany", "france", "spain", "italy", "netherlands", "toronto", "vancouver", "montreal", "ontario",
    "new zealand", "auckland", "china", "japan", "brazil", "malaysia", "bangladesh", "sri lanka", "nepal", "saudi arabia", "qatar",
)


def _has(n: str, words) -> str | None:
    for w in words:
        if f" {w} " in n:
            return w
    return None


def area_terms(ctx: dict) -> set[str]:
    """Every place name the client serves: city, service-area labels (and their parts), states (full name and abbreviation)."""
    out: set[str] = set()
    raw = [ctx.get("city") or ""] + [(a.get("label") if isinstance(a, dict) else str(a)) or "" for a in ctx.get("service_areas") or []]
    raw += [ctx.get("address") or ""]
    for s in ctx.get("states") or []:
        s = str(s)
        raw.append(s)
        full = next((k for k, v in US_STATES.items() if v.lower() == s.lower()), None)
        if full:
            raw.append(full)
        elif s.lower() in US_STATES:
            raw.append(US_STATES[s.lower()])
    for r in raw:
        for part in re.split(r"[,;/|]", r):
            p = normalize_term(re.sub(r"\d{5}(-\d{4})?", "", part))
            if p and len(p) >= 2:
                out.add(p)
                if p in US_STATES:
                    out.add(US_STATES[p].lower())
    return out


def place_in(keyword: str) -> str | None:
    """A US state, a major US city or a foreign country/city named in the keyword (longest match first), or None."""
    n = f" {normalize_term(keyword)} "
    for p in sorted([*MAJOR_CITIES, *US_STATES, *COUNTRIES], key=len, reverse=True):
        if f" {p} " in n:
            return p
    for w in n.split():
        if w in SAFE_ABBR:
            return w
    return None


def _served(place: str, areas: set[str]) -> bool:
    if place in areas:
        return True
    st = US_STATES.get(place)
    if st and st.lower() in areas:
        return True
    return any(place in a.split() or f" {place} " in f" {a} " or a == place for a in areas)


def location_relevance(keyword: str, areas: set[str]) -> tuple[str, str]:
    """('in_area' | 'outside_area' | 'none', reason)."""
    n = f" {normalize_term(keyword)} "
    hit = next((a for a in sorted(areas, key=len, reverse=True) if (len(a) > 2 or a.upper() in US_STATES.values()) and f" {a} " in n), None)
    if hit:
        return "in_area", f"names the service area “{hit}”"
    p = place_in(keyword)
    if p and not _served(p, areas):
        return "outside_area", f"names “{p}”, which is not one of the client's service areas"
    if p:
        return "in_area", f"names “{p}”, inside the service area"
    if " near me " in n:
        return "in_area", "“near me”: answered by location targeting"
    return "none", "no place named; location targeting decides where it shows"


def near_key(keyword: str) -> str:
    """Near-duplicate key: same words regardless of order, plural 's', and filler words."""
    filler = {"a", "an", "the", "in", "for", "of", "my", "to", "and", "near", "me"}
    words = [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in normalize_term(keyword).split() if w not in filler]
    return " ".join(sorted(words))


def classify(keyword: str, *, label: str, service: str | None, services: list[str], excluded: list[str], areas: set[str],
             brand: set[str], competitors: set[str], measured: bool) -> dict:
    """The structured classification of one keyword. `label` is the rule/AI service label (SERVICE / GENERIC / IRRELEVANT)."""
    n = f" {normalize_term(keyword)} "
    svc_words = {w for s in services for w in normalize_term(s).split()}
    bare = " " + " ".join(w for w in n.split() if w not in svc_words) + " "  # "dog training" is a service, not a job search
    loc, loc_why = location_relevance(keyword, areas)
    reasons: list[str] = []
    cls = None
    ex = next((e for e in excluded if normalize_term(e) and f" {normalize_term(e)} " in n), None)
    if ex:
        cls, why = "irrelevant_service", f"matches the excluded service “{ex}”"
    elif (j := _has(bare, JOB)):
        cls, why = "job_seeking", f"job-seeker wording (“{j}”)"
    elif loc == "outside_area":
        cls, why = "irrelevant_location", loc_why
    elif (c := next((c for c in competitors if c and f" {c} " in n), None)) or (c := _has(n, NAV_PLATFORMS)):
        cls, why = "competitor_navigation", f"names “{c}”: someone looking for that business or platform"
    elif brand and (b := next((b for b in brand if f" {b} " in n), None)):
        cls, why = "competitor_navigation", f"names the client's own brand (“{b}”): navigation, not new demand"
    elif (d := _has(bare, DIY)):
        cls, why = "diy_educational", f"do-it-yourself / learning wording (“{d}”)"
    elif label == "IRRELEVANT":
        cls, why = "irrelevant_service", "does not match any service the client offers"
    elif (i := _has(n, INFO)) or n.strip().endswith("?") or n.strip().startswith(("how ", "what ", "why ", "when ", "who ")):
        cls, why = "informational", f"question / research wording{f' (“{i}”)' if i else ''}"
    elif loc == "in_area" and label == "SERVICE":
        cls, why = "local_service", f"a client service {loc_why}"
    elif _has(n, TRANSACT):
        cls, why = "transactional", f"ready-to-act wording (“{_has(n, TRANSACT)}”)"
    elif label in ("SERVICE", "GENERIC") and (_has(n, HIGH_INTENT) or len(n.split()) >= 2):
        cls, why = "high_intent_commercial", "a client service with buying wording" if _has(n, HIGH_INTENT) else "names a client service"
    else:
        cls, why = "informational", "too broad to show buying intent"
    reasons.append(why)
    commercial = ("high" if cls in ("high_intent_commercial", "local_service", "transactional") else
                  "low" if cls == "informational" else "none")
    rejected = cls in REJECTED_CLASSES
    if rejected:
        status, reject = "rejected", why
    elif cls == "informational":
        status, reject = "research_only", "informational intent: kept as research, not targeted"
    else:
        status, reject = "eligible", None
    return {"class": cls, "class_label": CLASSES[cls], "class_reason": why, "location_relevance": loc, "location_reason": loc_why,
            "commercial_relevance": commercial, "selection_status": status, "rejection_reason": reject,
            "measurement_status": "measured" if measured else "unmeasured", "service": service}


def pre_reject(keyword: str, *, excluded: list[str], areas: set[str], competitors: set[str], services: list[str] = ()) -> str | None:
    """Reason a keyword is not worth measuring at all (outside the service area, job-seeking, excluded service, another business's name).
    Measurement is spent on keywords that can enter the campaign."""
    n = f" {normalize_term(keyword)} "
    if any(normalize_term(e) and f" {normalize_term(e)} " in n for e in excluded):
        return "excluded service"
    svc_words = {w for s in services for w in normalize_term(s).split()}
    if _has(" " + " ".join(w for w in n.split() if w not in svc_words) + " ", JOB):
        return "job-seeking"
    if location_relevance(keyword, areas)[0] == "outside_area":
        return "outside the service area"
    if any(c and f" {c} " in n for c in competitors) or _has(n, NAV_PLATFORMS):
        return "competitor / platform navigation"
    return None
