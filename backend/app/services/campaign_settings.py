"""Campaign-level settings that every Search campaign must state explicitly: target location, location targeting mode, language and ad
schedule. Each becomes a real Google Ads criterion in the payload (or an explicit "all days and hours" choice), and anything that cannot
be determined is a blocking issue with the fix, never a blank in the report."""
from __future__ import annotations

import re

LANGUAGES = {"en": ("English", "languageConstants/1000"), "es": ("Spanish", "languageConstants/1003"),
             "fr": ("French", "languageConstants/1002"), "de": ("German", "languageConstants/1001"),
             "zh": ("Chinese (simplified)", "languageConstants/1017"), "vi": ("Vietnamese", "languageConstants/1040"),
             "ko": ("Korean", "languageConstants/1012"), "pt": ("Portuguese", "languageConstants/1014")}
DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
_ABBR = {"mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "wednesday": 2, "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
         "fri": 4, "friday": 4, "sat": 5, "saturday": 5, "sun": 6, "sunday": 6}
ALL_HOURS = ("all", "all_hours", "24/7", "always")


def _days(spec: str) -> list[int]:
    s = spec.strip().lower().replace("–", "-")
    if s in ("daily", "every day", "everyday", "all", "mon-sun"):
        return list(range(7))
    if s in ("weekdays",):
        return list(range(5))
    if s in ("weekends", "weekend"):
        return [5, 6]
    out: list[int] = []
    for part in re.split(r"[,/& ]+", s):
        if "-" in part:
            a, b = (x.strip() for x in part.split("-", 1))
            if a in _ABBR and b in _ABBR:
                i, j = _ABBR[a], _ABBR[b]
                out += list(range(i, j + 1)) if i <= j else list(range(i, 7)) + list(range(0, j + 1))
        elif part in _ABBR:
            out.append(_ABBR[part])
    return sorted(set(out))


def _hm(t: str) -> tuple[int, int] | None:
    m = re.match(r"^\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*$", t.lower())
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if not (0 <= h <= 24 and mi in (0, 15, 30, 45)):
        return None
    return h, mi


MINUTE = {0: "ZERO", 15: "FIFTEEN", 30: "THIRTY", 45: "FORTY_FIVE"}


def parse_hours(hours: dict) -> tuple[list[dict], list[str]]:
    """Business hours ({"mon-fri": "07:00-19:00", "sun": "closed"}) → ad schedule entries, plus the entries that could not be read."""
    out, bad = [], []
    for k, v in (hours or {}).items():
        days = _days(str(k))
        val = str(v or "").strip().lower()
        if not days:
            bad.append(f"{k}: {v}")
            continue
        if val in ("closed", "off", ""):
            continue
        if val in ("24h", "24 hours", "open 24 hours", "24/7"):
            span = ((0, 0), (24, 0))
        else:
            parts = re.split(r"\s*(?:-|–|\bto\b)\s*", val, maxsplit=1)
            if len(parts) != 2 or _hm(parts[0]) is None or _hm(parts[1]) is None:
                bad.append(f"{k}: {v}")
                continue
            span = (_hm(parts[0]), _hm(parts[1]))
        (sh, sm), (eh, em) = span
        if (eh, em) <= (sh, sm):
            bad.append(f"{k}: {v} (end before start)")
            continue
        for d in days:
            out.append({"day_of_week": DAYS[d], "start_hour": sh, "start_minute": MINUTE[sm], "end_hour": eh, "end_minute": MINUTE[em]})
    return out, bad


def resolve(business, fields: dict) -> dict:
    """The explicit settings for this campaign and the blocking issues for anything missing."""
    issues: list[dict] = []
    # location
    zips = fields.get("zip_codes") or []
    radius = float(fields.get("radius_miles") or business.default_radius_miles or 20)
    address = (business.address or "").strip()
    if zips:
        location = {"mode": "zip_codes", "label": f"{len(zips)} ZIP code(s): {', '.join(zips[:6])}{'…' if len(zips) > 6 else ''}", "zip_codes": zips}
    elif address:
        location = {"mode": "radius", "label": f"{radius:g} mile radius around {address}", "radius_miles": radius, "address": address}
    else:
        location = {"mode": None, "label": None}
        issues.append({"code": "NO_TARGET_LOCATION", "field": "location", "blocking": True,
                       "message": "No target location: the business has no street address and no ZIP codes were given, so the campaign "
                                  "would target nowhere (or everywhere). Add the business address or ZIP codes in the client profile."})
    # language
    code = str(fields.get("language") or getattr(business, "language", None) or "en").lower()[:2]
    if code not in LANGUAGES:
        issues.append({"code": "UNKNOWN_LANGUAGE", "field": "language", "blocking": True, "message": f"Language “{code}” is not supported."})
        language = {"code": code, "label": None, "constant": None}
    else:
        language = {"code": code, "label": LANGUAGES[code][0], "constant": LANGUAGES[code][1]}
    # schedule
    choice = str(fields.get("ad_schedule") or "").strip().lower()
    entries, bad = parse_hours(getattr(business, "business_hours", None) or {})
    if choice in ALL_HOURS:
        schedule = {"mode": "all_hours", "label": "All days, all hours (chosen explicitly)", "entries": []}
    elif entries:
        days = sorted({e["day_of_week"] for e in entries}, key=DAYS.index)
        schedule = {"mode": "business_hours", "label": f"Business hours on {len(days)} day(s): " + ", ".join(
            f"{e['day_of_week'][:3].title()} {e['start_hour']:02d}:{_mm(e['start_minute'])}–{e['end_hour']:02d}:{_mm(e['end_minute'])}"
            for e in entries[:7]), "entries": entries, "unreadable": bad}
    else:
        schedule = {"mode": None, "label": None, "entries": [], "unreadable": bad}
        issues.append({"code": "NO_AD_SCHEDULE", "field": "ad_schedule", "blocking": True,
                       "message": ("Business hours could not be read (" + "; ".join(bad[:3]) + ")" if bad else "No business hours are recorded")
                                  + ": set the hours in the client profile, or set the campaign to run all days and hours."})
    return {"location": location, "location_targeting_mode": {"positive": "PRESENCE", "negative": "PRESENCE",
                                                              "label": "Presence: people in or regularly in the targeted area"},
            "language": language, "ad_schedule": schedule, "issues": issues}


def _mm(v: str) -> str:
    return {"ZERO": "00", "FIFTEEN": "15", "THIRTY": "30", "FORTY_FIVE": "45"}[v]


def criteria_ops(campaign_ref: str, settings: dict) -> list[dict]:
    """Language and ad schedule criteria (location criteria are built by planner.location_ops)."""
    ops = []
    if settings["language"].get("constant"):
        ops.append({"resource": "campaign_criterion", "op": "create",
                    "fields": {"campaign": campaign_ref, "type": "LANGUAGE", "language": {"language_constant": settings["language"]["constant"]}}})
    for e in settings["ad_schedule"].get("entries") or []:
        ops.append({"resource": "campaign_criterion", "op": "create", "fields": {"campaign": campaign_ref, "type": "AD_SCHEDULE", "ad_schedule": e}})
    return ops
