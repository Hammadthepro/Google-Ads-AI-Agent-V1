"""TEST FIXTURE ONLY — never imported by the application. Deterministic AI stand-in for isolated unit tests."""
from __future__ import annotations

import json
import re
import time

from app.adapters.ai import AIProvider, RawCompletion
from app.core.util import normalize_term
from app.services.keyword_labels import JUNK_WORDS, label_keyword  # noqa: F401
from tests.fakes.mock_world.failures import check_failure

# =====================================================================================================
# Mock provider
# =====================================================================================================
CLAIM_WORDS = ["licensed", "insured", "certified", "guarantee", "#1", "best", "top rated", "award", "same-day",
               "same day", "24/7", "no fee", "free", "family owned", "years", "since", "%", "$", "warranty",
               "board-certified", "cheapest", "lowest"]


def _fit(items, limit):
    out, seen = [], set()
    for t in items:
        t = t.strip()
        if t and len(t) <= limit and t.lower() not in seen:
            out.append(t)
            seen.add(t.lower())
    return out


def _title(s: str) -> str:
    return " ".join(w if w.isupper() else w.capitalize() for w in s.split())


def mock_ad_copy(service: str, city: str, business: str, claims: list[str], *, include_unverified: bool = False,
                 angle: str = "") -> dict:
    svc = _title(service)
    heads = [f"{svc} in {city}", f"{city} {svc}", f"Expert {svc}", f"Local {svc} Team", business,
             f"Book {svc} Today", "Request a Quote Today", "Speak With Our Team", "Schedule Your Visit Today",
             f"{svc} Done Right", "Clear Options, No Pressure", f"Trusted by {city} Locals"]
    if angle:
        heads.insert(0, angle)
    heads += [c for c in claims]
    if include_unverified:
        heads.insert(2, f"Rated #1 in {city}")
    descs = [f"Professional {service.lower()} for homes and businesses in {city}. Request a quote online today.",
             f"Talk to our team about your {service.lower()} needs. Clear options and honest advice.",
             f"{business} serves {city} and nearby areas. Call or book online in minutes."]
    if claims:
        descs.append((". ".join(claims[:2]) + f". Serving {city}.")[:90])
    slug = re.sub(r"[^a-z0-9]+", "-", service.lower()).strip("-")[:15].strip("-")
    return {"headlines": _fit(heads, 30)[:15], "descriptions": _fit(descs, 90)[:4], "path1": slug,
            "path2": re.sub(r"[^a-z0-9]+", "-", city.lower())[:15]}


class MockAIProvider(AIProvider):
    name = "MockAIProvider"
    source = "MOCK"

    def __init__(self, variant: str = "a"):
        self.variant = variant  # "b" = the mock stand-in for the second (Claude) side: a different but plausible plan

    def complete(self, purpose, system, user, *, model_role="draft") -> RawCompletion:
        started = time.monotonic()
        injected = check_failure("ai", purpose)
        if injected == "invalid_json":
            return RawCompletion(text='{"campaign_name": "broken", "ad_groups": [ {"name": ', model="mock-ai-v1")
        if injected == "schema_mismatch":
            return RawCompletion(text=json.dumps({"unexpected": True}), model="mock-ai-v1")
        handler = getattr(self, f"_h_{purpose.lower()}")
        data = handler(user)
        return RawCompletion(text=json.dumps(data), model="mock-ai-v1" if model_role == "draft" else "mock-ai-challenger-v1",
                             latency_ms=int((time.monotonic() - started) * 1000))

    # ---------------------------------------------------------------- handlers
    def _h_extract_services(self, u):
        services, claims, areas = [], [], set()
        for p in u.get("pages", []):
            m = re.match(r"(.+?) in ([A-Z][\w ]+)$", p.get("h1") or "")
            if m and p.get("url"):
                services.append({"name": m.group(1).strip(), "page_url": p["url"]})
                areas.add(m.group(2).strip())
            for sent in re.split(r"(?<=[.!])\s+", p.get("text") or ""):
                if any(w in sent.lower() for w in CLAIM_WORDS) and len(sent) < 120 and sent not in claims:
                    claims.append(sent.strip())
        brand = (u.get("business_name") or "").split()
        return {"services": services, "service_areas": sorted(areas), "claims": claims[:12], "brand_words": brand[:3]}

    def _h_agent_intent(self, u):
        """Deterministic stand-in: picks the allowed intent whose label shares the most words with the request."""
        words = set(re.findall(r"[a-z]+", u.get("request", "").lower()))
        best, score = "", 0
        for key, label in u.get("allowed_intents", {}).items():
            sc = len(words & set(re.findall(r"[a-z]+", (label + " " + key.replace("_", " ")).lower())))
            if sc > score:
                best, score = key, sc
        return {"intent": best, "reason": "word overlap"}

    def _h_label_keywords(self, u):
        out = []
        for kw in u.get("keywords", []):
            label, svc, why = label_keyword(kw, u.get("services", []), u.get("excluded_services", []))
            out.append({"keyword": kw, "label": label, "service": svc, "reason": why})
        return {"labels": out}

    def _h_extract_competitor_ads(self, u):
        text = u.get("ads_text", "")
        lines = [ln.strip() for ln in re.split(r"[\n|]", text) if ln.strip()]
        offers = [ln for ln in lines if re.search(r"\$|%|free|special|discount|off\b", ln, re.I)]
        claims = [ln for ln in lines if any(w in ln.lower() for w in CLAIM_WORDS) and ln not in offers]
        angles = []
        low = text.lower()
        for key, angle in (("24/7", "Always-available / emergency"), ("same", "Speed / same-day"), ("price", "Price transparency"),
                           ("review", "Social proof"), ("family", "Local / family owned"), ("warrant", "Risk reversal / warranty")):
            if key in low:
                angles.append(angle)
        return {"offers": offers[:10], "angles": angles, "claims": claims[:10]}

    def _ad_group(self, u, svc: dict, include_unverified=False) -> dict:
        city, biz = u.get("city", "your area"), u.get("business_name", "Our team")
        claims = [c for c in u.get("verified_claims", []) if len(c) <= 30]
        rows = [r for r in u.get("keyword_table", []) if r.get("label") == "SERVICE" and r.get("service") == svc["name"]]
        rows.sort(key=lambda r: -(r.get("avg_monthly_searches") or 0))
        kws = []
        for r in rows[:6]:
            kws.append({"text": r["keyword"], "match_type": "EXACT"})
            if len(kws) < 10:
                kws.append({"text": r["keyword"], "match_type": "PHRASE"})
        if not kws:
            kws = [{"text": svc["name"].lower(), "match_type": "EXACT"}, {"text": svc["name"].lower(), "match_type": "PHRASE"}]
        return {"name": svc["name"], "service": svc["name"], "landing_page": svc.get("landing_page") or u.get("website", ""),
                "keywords": kws, "negatives": [],
                "ads": [mock_ad_copy(svc["name"], city, biz, claims, include_unverified=include_unverified)]}

    def _variant_b(self, g: dict) -> dict:
        """Second opinion: adds a looser 'near me' phrase keyword and an extra unverified-claim headline."""
        g = dict(g)
        g["keywords"] = [*g["keywords"], {"text": "local contractors near me", "match_type": "PHRASE"}]
        ads = []
        for ad in g["ads"]:
            ad = dict(ad)
            ad["headlines"] = [*ad["headlines"][:1], "Top Rated Local Experts", *ad["headlines"][1:]][:15]
            ads.append(ad)
        g["ads"] = ads
        return g

    def _h_draft_campaign(self, u):
        services = u.get("services", [])
        groups = [self._ad_group(u, s, include_unverified=(i == 0 and self.variant == "a")) for i, s in enumerate(services)]
        if self.variant == "b":
            groups = [self._variant_b(g) for g in groups]
        negatives = sorted({r["keyword"] for r in u.get("keyword_table", []) if r.get("label") == "IRRELEVANT"})
        negatives = sorted(set(negatives) | {"jobs", "diy", "free"})
        share = round(100 / max(1, len(groups)), 1)
        tracking = u.get("tracking_state", "YELLOW")
        rules = " ".join(u.get("playbook_rules", []))
        website = u.get("website", "").rstrip("/")
        return {
            "campaign_name": f"{u.get('business_name', 'Business')} | {', '.join(s['name'] for s in services)[:60]} | Search",
            "bidding_strategy": "MAXIMIZE_CLICKS" if tracking == "OVERRIDE_RED" else "MAXIMIZE_CONVERSIONS",
            "budget_allocation": [{"ad_group": g["name"], "percent": share} for g in groups],
            "ad_groups": groups,
            "campaign_negatives": negatives[:40],
            "sitelinks": [{"text": "Request a Quote", "url": f"{website}/contact"},
                          *({"text": g["name"][:25], "url": g["landing_page"]} for g in groups[:3])],
            "callouts": [c for c in u.get("verified_claims", []) if len(c) <= 25][:4],
            "use_call_asset": bool(u.get("phone")),
            "ad_schedule_note": "Ads run 24/7 so forms are captured anytime; call asset limited to business hours.",
            "location_note": f"Presence targeting within {u.get('radius_miles', 20)} miles of {u.get('city', 'the business')}.",
            "rationale": ("One ad group per service keeps intent tight; exact and phrase match only (V1 policy); "
                          "irrelevant job/DIY/free searches are excluded up front." + (f" Playbook applied: {rules[:200]}" if rules else "")),
        }

    def _h_draft_ad_group(self, u):
        g = self._ad_group(u, u["service"])
        return self._variant_b(g) if self.variant == "b" else g

    def _h_draft_ad(self, u):
        current = u.get("current_ad")
        claims = [c for c in u.get("verified_claims", []) if len(c) <= 30]
        copy = mock_ad_copy(u["service"], u.get("city", ""), u.get("business_name", ""), claims,
                            angle=(u.get("change_request") or "")[:30])
        if current:
            keep = [h for h in current.get("headlines", []) if h not in copy["headlines"]][:5]
            copy["headlines"] = _fit(copy["headlines"][:10] + keep, 30)[:15]
        return copy

    def _h_fix_ads(self, u):
        bad_phrases = [e.get("phrase", "") for e in u.get("errors", []) if e.get("phrase")]
        fixed, changes = [], []
        for ad in u.get("ads", []):
            heads = []
            for h in ad.get("headlines", []):
                h2 = h.replace("!", "")
                if any(bp and bp.lower() in h2.lower() for bp in bad_phrases):
                    changes.append(f"removed headline '{h}' (unsupported or policy issue)")
                    continue
                if len(h2) > 30:
                    changes.append(f"shortened headline '{h}'")
                    h2 = h2[:30].rsplit(" ", 1)[0]
                heads.append(h2)
            while len(heads) < 3:
                heads.append(["Request a Quote Today", "Speak With Our Team", "Schedule Your Visit Today"][len(heads)])
            descs = []
            for d in ad.get("descriptions", []):
                if any(bp and bp.lower() in d.lower() for bp in bad_phrases):
                    changes.append("removed a description with an unsupported claim")
                    continue
                descs.append(d if len(d) <= 90 else d[:90].rsplit(" ", 1)[0])
            while len(descs) < 2:
                descs.append("Talk to our team today. Clear options and honest advice.")
            fixed.append({**ad, "headlines": _fit(heads, 30)[:15], "descriptions": descs[:4]})
        return {"ads": fixed, "changes": changes}

    def _h_challenge(self, u):
        d = u.get("draft", {})
        verified = " ".join(u.get("verified_claims", [])).lower()
        objections, agreements = [], []
        groups = d.get("ad_groups") or ([d] if "keywords" in d else [])
        ads = [a for g in groups for a in g.get("ads", [])] + ([d] if "headlines" in d else [])
        for a in ads:
            for h in a.get("headlines", []):
                hl = h.lower()
                for w in ("#1", "best", "top rated", "guarantee", "cheapest"):
                    if w in hl and w not in verified:
                        objections.append({"category": "UNSUPPORTED_CLAIM", "severity": "HIGH", "target": h,
                                           "message": f"'{h}' makes a claim ('{w}') that is not in the verified claims list.",
                                           "suggestion": "Remove it or add evidence to verified claims."})
        seen = {}
        for g in groups:
            for k in g.get("keywords", []):
                key = (normalize_term(k["text"]), k["match_type"])
                if key in seen and seen[key] != g["name"]:
                    objections.append({"category": "KEYWORD_OVERLAP", "severity": "MEDIUM", "target": k["text"],
                                       "message": f"'{k['text']}' appears in '{seen[key]}' and '{g['name']}'.",
                                       "suggestion": "Keep it in the most relevant ad group only."})
                seen[key] = g["name"]
                if k["match_type"] == "BROAD":
                    objections.append({"category": "WEAK_INTENT", "severity": "HIGH", "target": k["text"],
                                       "message": "Broad match is not allowed in main ad groups in V1.", "suggestion": "Use exact/phrase."})
            slug = normalize_term(g.get("service", "")).split(" ")[0]
            if slug and slug not in (g.get("landing_page") or "").lower():
                objections.append({"category": "LANDING_PAGE", "severity": "MEDIUM", "target": g.get("name", ""),
                                   "message": f"Landing page {g.get('landing_page')} may not be specific to {g.get('service')}.",
                                   "suggestion": "Use the dedicated service page if one exists."})
        negs = " ".join(d.get("campaign_negatives", [])).lower()
        if groups and "jobs" not in negs and d.get("campaign_name"):
            objections.append({"category": "MISSING_EXCLUSION", "severity": "MEDIUM", "target": "campaign",
                               "message": "No exclusion for job-seeker searches.", "suggestion": "Add 'jobs' as a negative."})
        feas = u.get("feasibility") or {}
        if feas.get("status") == "UNREALISTIC":
            objections.append({"category": "BUDGET", "severity": "HIGH", "target": "budget",
                               "message": f"Budget cannot realistically buy the weekly target (expected {feas.get('expected_weekly_conversions')} vs target {feas.get('weekly_target')}).",
                               "suggestion": "Lower the target, raise the budget, or narrow the services."})
        elif feas.get("status") == "PARTIALLY_FEASIBLE":
            objections.append({"category": "BUDGET", "severity": "LOW", "target": "budget",
                               "message": "Target is only reachable in the optimistic scenario.", "suggestion": "Set expectations with the client."})
        if u.get("tracking_state") in ("AMBER", "YELLOW", "UNKNOWN", "OVERRIDE"):
            objections.append({"category": "TRACKING", "severity": "LOW", "target": "tracking",
                               "message": "Tracking is not verified with real leads; automated conversion-based optimisation stays off.",
                               "suggestion": "Confirm a test lead is recorded in Google Ads."})
        if not any(o["category"] == "KEYWORD_OVERLAP" for o in objections) and groups:
            agreements.append("Ad groups are separated by service with no keyword overlap.")
        if not any(o["category"] == "UNSUPPORTED_CLAIM" for o in objections):
            agreements.append("Ad copy only uses verified claims.")
        verdict = "REVISE" if any(o["severity"] == "HIGH" for o in objections) else "APPROVE"
        return {"verdict": verdict, "objections": objections, "agreements": agreements}

    def _h_resolve_human_plan(self, u):
        res = []
        for diff in u.get("differences", []):
            res.append({"topic": diff["topic"], "human": str(diff.get("human")), "ai": str(diff.get("ai")),
                        "recommended_resolution": (
                            "Keep the human decision (applied by default). " +
                            ("AI suggests reconsidering: " + diff.get("ai_reason", "") if diff.get("ai_reason") else
                             "The AI alternative is shown for reference only."))})
        return {"disagreements": res, "summary": f"{len(res)} difference(s); human decisions are applied unless changed at approval."}

    def _h_diagnose(self, u):
        m = u.get("metrics", {})
        causes = []
        if u.get("tracking_state") not in ("GREEN",):
            causes.append("TRACKING")
        if (m.get("budget_lost_is") or 0) > 0.15:
            causes.append("BUDGET_LIMITED")
        if (m.get("rank_lost_is") or 0) > 0.35:
            causes.append("RANK_LIMITED")
        if m.get("ctr") is not None and m["ctr"] < 0.04:
            causes.append("LOW_CTR")
        if m.get("cvr") is not None and m["cvr"] < 0.05:
            causes.append("LOW_CVR")
        if (m.get("missed_call_rate") or 0) > 0.25:
            causes.append("CALLS_MISSED")
        if m.get("cpc") and m.get("cpc_benchmark") and m["cpc"] > 1.3 * m["cpc_benchmark"]:
            causes.append("HIGH_CPC")
        if (u.get("open_fixes") or 0) > 0:
            causes.append("OPEN_CLIENT_FIXES")
        return {"causes": causes, "explanation": "Causes inferred from the computed metrics provided."}

    def _h_report_summary(self, u):
        t = u.get("totals", {})
        gap = t.get("gap", 0)
        lead = (f"{u.get('client_name', 'The client')} recorded {t.get('conversions', 0)} conversions against a weekly target of "
                f"{t.get('target', 0)}" + (f", {abs(gap)} short." if gap < 0 else ", on or above target."))
        hl = [f"Spend ${t.get('cost', 0):,.2f}; wasted spend ${t.get('wasted_spend', 0):,.2f}."]
        if u.get("recommended_actions"):
            hl.append("Recommended: " + "; ".join(u["recommended_actions"][:3]))
        if u.get("warnings"):
            hl.append("Warnings: " + "; ".join(u["warnings"][:3]))
        return {"summary": lead, "highlights": hl}

    def _h_explain(self, u):
        return {"summary": u.get("headline") or "Explanation generated from application data.",
                "highlights": [f"{k}: {v}" for k, v in list((u.get("facts") or {}).items())[:6]]}

    def _h_audit_summary(self, u):
        f = u.get("findings", [])
        return {"summary": f"{len(f)} finding(s) for {u.get('subject', 'the business')}.", "highlights": [str(x) for x in f[:6]]}


