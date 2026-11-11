"""AI competition: the internal AI and Claude receive IDENTICAL inputs, draft independently, cross-challenge each
other's draft, and a RULE-BASED consolidator builds the final plan from evidence.

Principles
- Both sides get the same payload, the same prompt template id and version (recorded per AIRun).
- Agreement between the two models is a signal, never proof. Every kept item must pass evidence rules.
- The consolidator never calls an AI. Numbers (budget split, bidding) are decided by code.
- If one side fails, the competition degrades to a single draft and says so — no fabricated second opinion.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.adapters import registry
from app.core.util import normalize_term
from app.services import ai_service

SIDES = ("internal", "claude")
CLAIM_TRIGGERS = ("#1", "best", "top rated", "guarantee", "cheapest", "lowest", "award", "free", "no fee")
PHONE = re.compile(r"\d{3}[\s.\-)]*\d{3}[\s.\-]*\d{4}")


def _label(side: str, provider) -> str:
    return f"{side}:{'live' if provider.source == 'LIVE' else 'test'}"


class _Unavailable:
    """Stands in for a side whose integration is not configured, so the other side still runs (single-AI mode, clearly labelled)."""
    source, name = "NONE", "not configured"

    def __init__(self, exc):
        self.error = exc


def _providers() -> dict:
    """Both AIs are independent integrations. A missing one is recorded, not fatal; if BOTH are missing the first error is raised."""
    from app.core.errors import IntegrationNotConfigured
    out = {}
    for side, name in (("internal", "ai"), ("claude", "claude_ai")):
        try:
            out[side] = registry.get(name)
        except IntegrationNotConfigured as exc:
            out[side] = _Unavailable(exc)
    if all(isinstance(p, _Unavailable) for p in out.values()):
        raise out["internal"].error
    return out


# ------------------------------------------------------------------------------------------ ad scoring
def ad_problems(ad: dict, verified_claims: list[str]) -> list[str]:
    verified = " ".join(verified_claims).lower()
    out = []
    for h in ad.get("headlines", []):
        if len(h) > 30:
            out.append(f"headline too long: {h}")
        if "!" in h:
            out.append(f"exclamation mark: {h}")
        if PHONE.search(h):
            out.append(f"phone number in headline: {h}")
        low = h.lower()
        out += [f"unverified claim '{w}': {h}" for w in CLAIM_TRIGGERS if w in low and w not in verified]
    for d in ad.get("descriptions", []):
        if len(d) > 90:
            out.append(f"description too long: {d[:30]}…")
        low = d.lower()
        out += [f"unverified claim '{w}' in description" for w in CLAIM_TRIGGERS if w in low and w not in verified]
    return out


def _clean_headlines(ad: dict, verified_claims: list[str]) -> list[str]:
    """Headlines that individually pass the editorial and claim rules."""
    keep = []
    for h in ad.get("headlines", []):
        if not ad_problems({"headlines": [h], "descriptions": []}, verified_claims):
            keep.append(h)
    return keep


def choose_ad(a: dict | None, b: dict | None, verified_claims: list[str]) -> tuple[dict, dict]:
    """Pick the stronger ad by evidence (fewest rule problems, then most valid headlines); tie → internal.
    Then top up the winner with the loser's valid, distinct headlines (max 15)."""
    cands = {s: ad for s, ad in (("internal", a), ("claude", b)) if ad}
    scored = {s: (len(ad_problems(ad, verified_claims)), -len(_clean_headlines(ad, verified_claims))) for s, ad in cands.items()}
    winner = min(scored, key=lambda s: (scored[s], s != "internal"))
    base = dict(cands[winner])
    heads = _clean_headlines(base, verified_claims)
    dropped = [h for h in base.get("headlines", []) if h not in heads]
    added = []
    for s, ad in cands.items():
        if s == winner:
            continue
        for h in _clean_headlines(ad, verified_claims):
            if h.lower() not in {x.lower() for x in heads} and len(heads) < 15:
                heads.append(h)
                added.append(h)
    descs = [d for d in base.get("descriptions", []) if not ad_problems({"headlines": [], "descriptions": [d]}, verified_claims)]
    for s, ad in cands.items():
        if s != winner:
            for d in ad.get("descriptions", []):
                if d not in descs and len(descs) < 4 and not ad_problems({"headlines": [], "descriptions": [d]}, verified_claims):
                    descs.append(d)
    if len(heads) < 3 or len(descs) < 2:  # never emit an ad the schema/validator would reject
        return base, {"winner": winner, "scores": scored, "fallback": "kept winner unchanged: too few valid lines after filtering"}
    base["headlines"], base["descriptions"] = heads[:15], descs[:4]
    return base, {"winner": winner, "scores": {k: {"problems": v[0], "valid_headlines": -v[1]} for k, v in scored.items()},
                  "dropped_headlines": dropped, "added_from_other_side": added}


# ------------------------------------------------------------------------------------------ keyword evidence
def keyword_evidence(table: list[dict]) -> tuple[dict[str, set[str]], set[str]]:
    """service name → normalized SERVICE keywords from research; set of IRRELEVANT keywords."""
    by_service: dict[str, set[str]] = {}
    irrelevant: set[str] = set()
    for r in table:
        n = normalize_term(r.get("keyword", ""))
        if r.get("label") == "SERVICE":
            by_service.setdefault(normalize_term(r.get("service") or ""), set()).add(n)
        elif r.get("label") == "IRRELEVANT":
            irrelevant.add(n)
    return by_service, irrelevant


def _kw_ok(text: str, service: str, evidence: dict[str, set[str]], irrelevant: set[str]) -> tuple[bool, str]:
    n = normalize_term(text)
    if not n:
        return False, "empty"
    if n in irrelevant or any(w in irrelevant for w in n.split()):
        return False, "labelled irrelevant by research"
    if n in evidence.get(normalize_term(service), set()):
        return True, "research: SERVICE keyword for this service"
    sv = set(normalize_term(service).split())
    if sv and sv <= set(n.split()):
        return True, "contains the full service name"
    return False, "no research evidence for this service"


def consolidate_group(side_groups: dict[str, dict], table: list[dict], verified_claims: list[str], seen: set,
                      limit: int = 10) -> tuple[dict, dict]:
    ref = side_groups.get("internal") or side_groups.get("claude")
    service = ref["service"]
    evidence, irrelevant = keyword_evidence(table)
    cands: dict[tuple[str, str], set[str]] = {}
    order: list[tuple[str, str]] = []
    for side, g in side_groups.items():
        for k in g.get("keywords", []):
            key = (normalize_term(k["text"]), k["match_type"])
            if key not in cands:
                order.append(key)
            cands.setdefault(key, set()).add(side)
    kept, dropped = [], []
    for key in order:
        text, mt = key
        if mt == "BROAD":
            dropped.append({"keyword": text, "match_type": mt, "reason": "broad match is only allowed as an approved experiment"})
            continue
        ok, why = _kw_ok(text, service, evidence, irrelevant)
        if not ok:
            dropped.append({"keyword": text, "match_type": mt, "proposed_by": sorted(cands[key]), "reason": why})
            continue
        if key in seen:
            dropped.append({"keyword": text, "match_type": mt, "reason": "already used in another ad group (overlap)"})
            continue
        if len(kept) >= limit:
            dropped.append({"keyword": text, "match_type": mt, "reason": f"over the {limit}-keyword cap"})
            continue
        seen.add(key)
        kept.append({"text": text, "match_type": mt, "proposed_by": sorted(cands[key]), "evidence": why})
    first_ad = lambda g: (g.get("ads") or [None])[0] if g else None  # noqa: E731
    if any(g.get("ads") for g in side_groups.values()):
        ad, ad_info = choose_ad(first_ad(side_groups.get("internal")), first_ad(side_groups.get("claude")), verified_claims)
    else:
        ad, ad_info = {}, {}
    out = {"name": ref["name"], "service": service, "landing_page": ref["landing_page"],
           "keywords": [{"text": k["text"], "match_type": k["match_type"]} for k in kept],
           "negatives": sorted({normalize_term(n) for g in side_groups.values() for n in g.get("negatives", [])}),
           "ads": [ad] if ad else []}
    info = {"service": service, "kept": kept, "dropped": dropped, "ad": ad_info,
            "keyword_agreement": _agreement({k for k, v in cands.items() if "internal" in v},
                                            {k for k, v in cands.items() if "claude" in v})}
    return out, info


def _agreement(a: set, b: set) -> dict:
    inter, union = a & b, a | b
    return {"both": len(inter), "internal_only": len(a - b), "claude_only": len(b - a),
            "jaccard": round(len(inter) / len(union), 2) if union else 1.0}


# ------------------------------------------------------------------------------------------ comparison
def compare(drafts: dict[str, dict]) -> dict:
    """Structural comparison of the two raw drafts (before consolidation)."""
    a, b = drafts.get("internal"), drafts.get("claude")
    if not (a and b):
        return {"comparable": False}

    def groups(d):
        return {normalize_term(g["service"]): g for g in (d.get("ad_groups") or ([d] if "keywords" in d else []))}
    ga, gb = groups(a), groups(b)
    kws = lambda gs: {(normalize_term(k["text"]), k["match_type"]) for g in gs.values() for k in g.get("keywords", [])}  # noqa: E731
    return {"comparable": True, "ad_groups": _agreement(set(ga), set(gb)), "keywords": _agreement(kws(ga), kws(gb)),
            "bidding": {"internal": a.get("bidding_strategy"), "claude": b.get("bidding_strategy"),
                        "same": a.get("bidding_strategy") == b.get("bidding_strategy")},
            "negatives": _agreement({normalize_term(x) for x in a.get("campaign_negatives", [])},
                                    {normalize_term(x) for x in b.get("campaign_negatives", [])}),
            "note": "Agreement between models is a signal, not proof. Every item in the final plan passed rule-based evidence checks."}


# ------------------------------------------------------------------------------------------ run
def _draft_side(db, side, provider, purpose, payload, task_id):
    try:
        parsed, run = ai_service.generate(db, purpose, payload, task_id=task_id, provider=provider,
                                          model_role="draft", label=_label(side, provider))
        return parsed.model_dump(), run, None
    except Exception as exc:  # noqa: BLE001 - a failing side must not abort the other
        return None, None, f"{type(exc).__name__}: {getattr(exc, 'message', str(exc))[:300]}"


def _challenge_side(db, challenger_side, provider, target_draft, ctx, task_id):
    payload = {"draft": target_draft, **ctx}
    try:
        parsed, run = ai_service.generate(db, "CHALLENGE", payload, task_id=task_id, provider=provider,
                                          model_role="challenger", label=_label(challenger_side, provider))
        return parsed.model_dump(), run, None
    except Exception as exc:  # noqa: BLE001
        return None, None, f"{type(exc).__name__}: {getattr(exc, 'message', str(exc))[:300]}"


def run(db: Session, *, scope: str, payload: dict, challenge_ctx: dict, table: list[dict], verified_claims: list[str],
        conversion_bidding: bool | None, task_id: str | None) -> dict:
    """Returns {draft, competition, internal_run_id, challenger_run_id, challenger(merged report)}."""
    purpose = {"CAMPAIGN": "DRAFT_CAMPAIGN", "AD_GROUP": "DRAFT_AD_GROUP"}[scope]
    provs = _providers()
    drafts, runs, errors = {}, {}, {}
    for side in SIDES:
        if isinstance(provs[side], _Unavailable):
            errors[side] = provs[side].error.message  # "<AI> integration is not configured. Please configure…"
            continue
        d, r, e = _draft_side(db, side, provs[side], purpose, payload, task_id)
        if d:
            drafts[side], runs[side] = d, r
        else:
            errors[side] = e
    if "internal" not in drafts and "claude" not in drafts:
        raise ai_service.AIOutputInvalid("Neither AI produced a valid draft; nothing was created", details=errors)

    challenges = {}
    if len(drafts) == 2:  # cross-challenge: each side reviews the OTHER side's draft
        for challenger, target in (("internal", "claude"), ("claude", "internal")):
            rep, run_, err = _challenge_side(db, challenger, provs[challenger], drafts[target], challenge_ctx, task_id)
            if rep:
                challenges[f"{challenger}_on_{target}"] = {"report": rep, "run_id": run_.id}
            else:
                errors[f"challenge_{challenger}"] = err
    else:
        only = next(iter(drafts))
        rep, run_, err = _challenge_side(db, only, provs[only], drafts[only], challenge_ctx, task_id)
        if rep:
            challenges[f"{only}_on_{only}"] = {"report": rep, "run_id": run_.id}

    # ---- rule-based consolidation
    seen: set = set()
    notes = []
    if scope == "CAMPAIGN":
        service_names: list[str] = []
        for side in SIDES:
            for g in drafts.get(side, {}).get("ad_groups", []):
                if normalize_term(g["service"]) not in [normalize_term(x) for x in service_names]:
                    service_names.append(g["service"])
        allowed = {normalize_term(s["name"]) for s in payload.get("services", [])}
        groups, infos = [], []
        for svc in service_names:
            if allowed and normalize_term(svc) not in allowed:
                notes.append(f"Dropped ad group for '{svc}': not one of the client's active services")
                continue
            sg = {side: next((g for g in drafts[side]["ad_groups"] if normalize_term(g["service"]) == normalize_term(svc)), None)
                  for side in drafts}
            sg = {k: v for k, v in sg.items() if v}
            g, info = consolidate_group(sg, table, verified_claims, seen)
            groups.append(g)
            infos.append(info)
        base = drafts.get("internal") or drafts["claude"]
        other = drafts.get("claude") if "internal" in drafts else None
        share = round(100 / max(1, len(groups)), 1)
        negatives = sorted({normalize_term(n) for d in drafts.values() for n in d.get("campaign_negatives", [])}
                           | {normalize_term(r["keyword"]) for r in table if r.get("label") == "IRRELEVANT"} | {"jobs", "diy", "free"})
        active_kw = {k["text"] for g in groups for k in g["keywords"]}
        negatives = [n for n in negatives if n and n not in active_kw]
        callouts = [c for c in {*(base.get("callouts") or []), *((other or {}).get("callouts") or [])}
                    if len(c) <= 25 and any(normalize_term(c) in normalize_term(v) or normalize_term(v) in normalize_term(c)
                                            for v in verified_claims)]
        final = {**base, "bidding_strategy": "MAXIMIZE_CLICKS" if conversion_bidding is False else "MAXIMIZE_CONVERSIONS",  # False = confirmed: nothing is recording
                 "ad_groups": groups, "campaign_negatives": negatives[:60],
                 "budget_allocation": [{"ad_group": g["name"], "percent": share} for g in groups],
                 "callouts": sorted(callouts)[:4],
                 "rationale": ("Consolidated by rules from two independent AI drafts. Keywords kept only with research evidence "
                               "(exact/phrase only, no cross-group overlap); ads chosen by fewest rule problems; claims limited to the "
                               "verified list; bidding and budget split set by code.")}
        if other:
            known = {s["text"].lower() for s in base.get("sitelinks", [])}
            final["sitelinks"] = [*base.get("sitelinks", []), *[s for s in other.get("sitelinks", []) if s["text"].lower() not in known]][:4]
    else:
        base_g = {s: d for s, d in drafts.items()}
        final, info = consolidate_group(base_g, table, verified_claims, seen)
        infos = [info]

    comp = {"mode": "dual" if len(drafts) == 2 else "single",
            "providers": {s: {"provider": provs[s].name, "source": provs[s].source,
                              "model": runs[s].model if s in runs else None, "run_id": runs[s].id if s in runs else None,
                              "prompt": f"{runs[s].prompt_template_id}@{runs[s].prompt_version}" if s in runs else None}
                          for s in SIDES},
            "identical_inputs": True, "input_hash": next(iter(runs.values())).input_hash,
            "drafts": drafts, "challenges": challenges, "errors": errors, "comparison": compare(drafts),
            "consolidation": {"rules": ["exact/phrase keywords only", "keyword needs research evidence for its service",
                                        "no keyword in two ad groups", "claims only from verified list",
                                        "ad chosen by fewest policy/claim problems, tie → internal AI",
                                        "bidding and budget split decided by code"],
                              "groups": infos, "notes": notes},
            "agreement_is_not_proof": True}
    if comp["mode"] == "single":
        comp["degraded_reason"] = "; ".join(f"{k}: {v}" for k, v in errors.items() if k in SIDES) or "second AI unavailable"

    merged = merge_challenges(challenges, final)
    return {"draft": final, "competition": comp, "challenger": merged,
            "draft_run_id": (runs.get("internal") or runs.get("claude")).id,
            "challenger_run_id": next((v["run_id"] for v in challenges.values()), None)}


def merge_challenges(challenges: dict, final: dict) -> dict:
    """One report for the approval card. Objections about items that consolidation removed are marked resolved."""
    present = " ".join(
        [h for g in final.get("ad_groups", [final]) for ad in g.get("ads", []) for h in ad.get("headlines", [])]
        + [k["text"] for g in final.get("ad_groups", [final]) for k in g.get("keywords", [])]).lower()
    objections, agreements = [], []
    for who, c in challenges.items():
        for o in c["report"]["objections"]:
            target = (o.get("target") or "").lower()
            resolved = bool(target) and target not in present and o["category"] in ("UNSUPPORTED_CLAIM", "WEAK_INTENT",
                                                                                   "IRRELEVANT_KEYWORD", "KEYWORD_OVERLAP", "POLICY")
            objections.append({**o, "raised_by": who, "resolved_by_consolidation": resolved})
        agreements += c["report"]["agreements"]
    open_high = [o for o in objections if o["severity"] == "HIGH" and not o["resolved_by_consolidation"]]
    return {"verdict": "REVISE" if open_high else "APPROVE", "objections": objections,
            "agreements": sorted(set(agreements))}
