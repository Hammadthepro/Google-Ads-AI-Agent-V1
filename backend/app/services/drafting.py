"""Campaign drafting within a provider's token limits.

One request when it fits; otherwise controlled stages that share one fixed campaign plan:

  1. DRAFT_CAMPAIGN_SPEC     campaign name, bidding, budget split, the ad groups (name / service / landing page), campaign negatives,
                             sitelinks, callouts, notes. Everything campaign-level is decided here, once.
  2. DRAFT_AD_GROUP_CONTENT  keywords, negatives and the ad for a batch of the planned ad groups, sized to the token budget. The schema
                             of this stage cannot express a budget, bidding, targeting or a new ad group; names must match the plan.
  3. Assembly (code)         plan + contents -> the complete draft, validated against the full CampaignDraft schema. Any failed stage
                             fails the whole draft: a partial draft is never returned.

"Fits" is decided from (a) the prompt estimate against the provider's per-minute token limit (from configuration or the provider's own
rate-limit headers) and (b) the expected answer size against the configured output budget. A request the provider refuses as too large,
or an answer cut off at the output limit, is never repeated as-is: the work is split into smaller requests instead.
"""
from __future__ import annotations

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.adapters.ai.prompts import PROMPTS
from app.adapters.ai.schemas import CampaignDraft
from app.core.config import get_settings
from app.core.util import normalize_term, payload_hash
from app.models import AIRun
from app.services import ai_service, research_events

BUSINESS_KEYS = ("business_name", "website", "city", "phone", "radius_miles", "verified_claims", "brand_rules", "excluded_services",
                 "playbook_rules")
TOP_KEYWORDS = (8, 4, 0)   # strongest keywords per service shown to the planning stage, reduced only if the plan request is too large


def expected_output(n_groups: int) -> int:
    s = get_settings()
    return int(s.AI_OUTPUT_TOKENS_BASE + n_groups * s.AI_OUTPUT_TOKENS_PER_AD_GROUP)


def single_request_fits(provider, payload: dict) -> tuple[bool, str]:
    """Whether the whole campaign can be drafted in one request, and if not, why (in plain words)."""
    s = get_settings()
    n = len(payload.get("services") or [])
    need, cap = expected_output(n), int(s.AI_DRAFT_MAX_OUTPUT_TOKENS)
    if need > cap:
        return False, f"{n} ad groups need about {need:,} output tokens, more than the {cap:,}-token output budget of one request"
    budget = ai_service.budget_for(provider)
    est = ai_service.request_tokens("DRAFT_CAMPAIGN", payload)
    if budget and est > budget:
        return False, f"the one-request draft is ~{est:,} tokens, more than the ~{budget:,}-token per-minute budget"
    return True, ""


def draft_campaign(db: Session, payload: dict, *, provider, label: str, task_id: str | None) -> tuple[CampaignDraft, AIRun, dict]:
    """Returns (complete validated draft, the AIRun that holds it, info {mode, reason, runs})."""
    fits, reason = single_request_fits(provider, payload)
    if fits:
        try:
            parsed, run = ai_service.generate(db, "DRAFT_CAMPAIGN", payload, task_id=task_id, provider=provider, label=label,
                                              max_output_tokens=int(get_settings().AI_DRAFT_MAX_OUTPUT_TOKENS), split_on_truncation=True)
            return parsed, run, {"mode": "single", "runs": [run.id]}
        except (ai_service.TokenBudgetExceeded, ai_service.OutputTruncated) as exc:
            reason = exc.message.removesuffix("; nothing was executed").removesuffix("; nothing was used").removesuffix("; nothing was sent")
    research_events.emit("note", f"{label}: drafting in stages ({reason})", stage="draft", provider=label, reason=reason)
    return _staged(db, payload, provider=provider, label=label, task_id=task_id, reason=reason)


# ------------------------------------------------------------------------------------------------------------- stages
def _keywords(payload: dict) -> dict:
    """The compact keyword input ({keywords_by_service, irrelevant_terms}), whichever shape the caller passed."""
    kt = payload.get("keyword_table") or {}
    if isinstance(kt, list):
        from app.services.planner import compact_keywords
        kt = compact_keywords(kt)
    return kt


def _base(payload: dict) -> dict:
    return {k: payload[k] for k in BUSINESS_KEYS if k in payload}


def _spec_payload(payload: dict, top: int) -> dict:
    kt = _keywords(payload)
    kws = kt.get("keywords_by_service") or {}
    services = [{"name": x["name"], "landing_page": x.get("landing_page", ""), "researched_keywords": len(kws.get(x["name"], [])),
                 **({"top_keywords": kws.get(x["name"], [])[:top]} if top else {})} for x in payload.get("services") or []]
    out = {**_base(payload), "measurement": payload.get("measurement"), "monthly_budget": payload.get("monthly_budget"),
           "feasibility": payload.get("feasibility"), "note": payload.get("note", ""), "services": services,
           "irrelevant_terms": kt.get("irrelevant_terms") or []}
    generic = kws.get("Generic")
    if generic and top:
        out["generic_keywords"] = generic[:top]
    return out


def _spec_check(services: list[dict]):
    want = {normalize_term(x["name"]): x for x in services}

    def check(spec) -> list[str]:
        errs = []
        seen = {}
        for g in spec.ad_groups:
            key = normalize_term(g.service)
            if key not in want:
                errs.append(f"ad group '{g.name}' uses service '{g.service}', which is not one of the listed services")
                continue
            if key in seen:
                errs.append(f"service '{g.service}' has more than one ad group")
            seen[key] = g
            page = want[key].get("landing_page") or ""
            if page and g.landing_page != page:
                errs.append(f"ad group '{g.name}' must use the landing page {page}")
        missing = [x["name"] for k, x in want.items() if k not in seen]
        if missing:
            errs.append("every listed service needs one ad group; missing: " + ", ".join(missing))
        names = [g.name for g in spec.ad_groups]
        if len(set(map(normalize_term, names))) != len(names):
            errs.append("ad group names must be unique")
        unknown = [b.ad_group for b in spec.budget_allocation if normalize_term(b.ad_group) not in {normalize_term(n) for n in names}]
        if unknown:
            errs.append("budget_allocation names ad groups that are not in the plan: " + ", ".join(unknown))
        return errs
    return check


def _batch_payload(payload: dict, spec, batch: list) -> dict:
    kws = _keywords(payload).get("keywords_by_service") or {}
    names = {g.name for g in batch}
    return {**_base(payload),
            "campaign": {"campaign_name": spec.campaign_name, "bidding_strategy": spec.bidding_strategy, "location_note": spec.location_note,
                         "campaign_negatives": spec.campaign_negatives},
            "other_ad_groups": [{"name": g.name, "service": g.service} for g in spec.ad_groups if g.name not in names],
            "ad_groups": [{"name": g.name, "service": g.service, "landing_page": g.landing_page,
                           "researched_keywords": kws.get(g.service, [])} for g in batch]}


def _batch_check(batch: list):
    want = [g.name for g in batch]

    def check(res) -> list[str]:
        got = [g.name for g in res.ad_groups]
        errs = []
        if sorted(got) != sorted(want):
            errs.append(f"return exactly these ad groups, once each: {', '.join(want)} (got: {', '.join(got) or 'none'})")
        return errs
    return check


def _batches(payload: dict, spec, budget: int | None) -> list[list]:
    """Pack the planned ad groups, in order, into requests that fit both the prompt budget and the per-stage output budget."""
    cap = int(get_settings().AI_STAGE_MAX_OUTPUT_TOKENS)
    out, cur = [], []
    for g in spec.ad_groups:
        trial = [*cur, g]
        too_big_out = expected_output(len(trial)) > cap
        too_big_in = bool(budget) and ai_service.request_tokens("DRAFT_AD_GROUP_CONTENT", _batch_payload(payload, spec, trial)) > budget
        if cur and (too_big_out or too_big_in):
            out.append(cur)
            cur = [g]
        else:
            cur = trial
    if cur:
        out.append(cur)
    return out


def _stage_name(spec, batch: list) -> str:
    idx = [spec.ad_groups.index(g) + 1 for g in batch]
    return f"ad group {idx[0]}" if len(idx) == 1 else f"ad groups {idx[0]}-{idx[-1]}"


def _content(db, payload, spec, batch, *, provider, label, task_id, runs: list) -> dict:
    """Keywords/negatives/ads for `batch`. A cut-off answer or an over-limit request splits the batch in two (never repeats it)."""
    try:
        res, run = ai_service.generate(db, "DRAFT_AD_GROUP_CONTENT", _batch_payload(payload, spec, batch), task_id=task_id, provider=provider,
                                       label=label, max_output_tokens=int(get_settings().AI_STAGE_MAX_OUTPUT_TOKENS),
                                       stage=_stage_name(spec, batch), split_on_truncation=True, check=_batch_check(batch))
        runs.append(run)
        return {g.name: g for g in res.ad_groups}
    except (ai_service.TokenBudgetExceeded, ai_service.OutputTruncated) as exc:
        if len(batch) == 1:
            raise ai_service.AIOutputInvalid(f"Drafting stopped at {_stage_name(spec, batch)} ('{batch[0].name}'): "
                                             f"{exc.message.removesuffix('; nothing was executed')}. A single ad group does not fit the "
                                             "token budget; nothing was created", details={**exc.details, "stage": _stage_name(spec, batch)}) from exc
        half = len(batch) // 2
        research_events.emit("note", f"{label}: splitting {_stage_name(spec, batch)} into smaller requests", stage="draft", provider=label)
        return {**_content(db, payload, spec, batch[:half], provider=provider, label=label, task_id=task_id, runs=runs),
                **_content(db, payload, spec, batch[half:], provider=provider, label=label, task_id=task_id, runs=runs)}


def _staged(db, payload, *, provider, label, task_id, reason: str) -> tuple[CampaignDraft, AIRun, dict]:
    budget = ai_service.budget_for(provider)
    runs: list[AIRun] = []
    # 1. the shared plan (reduce only the keyword preview if the plan request itself is too large; nothing else is cut)
    spec_payload = None
    for top in TOP_KEYWORDS:
        spec_payload = _spec_payload(payload, top)
        if not budget or ai_service.request_tokens("DRAFT_CAMPAIGN_SPEC", spec_payload) <= budget:
            break
    spec, spec_run = ai_service.generate(db, "DRAFT_CAMPAIGN_SPEC", spec_payload, task_id=task_id, provider=provider, label=label,
                                         max_output_tokens=int(get_settings().AI_STAGE_MAX_OUTPUT_TOKENS), stage="campaign plan",
                                         check=_spec_check(payload.get("services") or []))
    runs.append(spec_run)
    research_events.emit("note", f"{label}: campaign plan fixed with {len(spec.ad_groups)} ad group(s); writing their keywords and ads",
                         stage="draft", provider=label, ad_groups=[g.name for g in spec.ad_groups][:30])
    # 2. contents, in budget-sized batches, against the fixed plan
    contents: dict = {}
    for batch in _batches(payload, spec, budget):
        contents.update(_content(db, payload, spec, batch, provider=provider, label=label, task_id=task_id, runs=runs))
    # 3. deterministic assembly + the complete schema
    groups = []
    for g in spec.ad_groups:
        c = contents.get(g.name)
        if c is None:  # cannot happen after the batch checks; never assemble around a gap
            raise ai_service.AIOutputInvalid(f"No content was produced for ad group '{g.name}'; nothing was created",
                                             details={"error_kind": "schema_invalid"})
        groups.append({"name": g.name, "service": g.service, "landing_page": g.landing_page,
                       "keywords": [k.model_dump() for k in c.keywords], "negatives": c.negatives, "ads": [a.model_dump() for a in c.ads]})
    assembled = {**spec.model_dump(exclude={"ad_groups"}), "ad_groups": groups}
    try:
        draft = CampaignDraft.model_validate(assembled)
    except ValidationError as exc:
        raise ai_service.AIOutputInvalid(f"The assembled campaign draft failed the campaign schema; nothing was created: {str(exc)[:600]}",
                                         details={"error_kind": "schema_invalid"}) from exc
    spec_t = PROMPTS["DRAFT_CAMPAIGN"]
    record = AIRun(task_id=task_id, purpose="DRAFT_CAMPAIGN", provider=label[:20], model=spec_run.model,
                   prompt_template_id=f"{spec_t['id']}_staged", prompt_version="1.0", input_hash=payload_hash(payload),
                   attempt=1, stage="assembled", provider_status="assembled", validation_ok=True, parsed_output=draft.model_dump(),
                   tokens_in=sum(r.tokens_in for r in runs), tokens_out=sum(r.tokens_out for r in runs),
                   latency_ms=sum(r.latency_ms for r in runs), finish_reason="stop",
                   raw_output=f"Assembled by code from {len(runs)} validated stage answers: " + ", ".join(r.id for r in runs))
    db.add(record)
    db.flush()
    research_events.emit("ai_result", f"{label} returned a valid draft campaign (assembled from {len(runs)} stages)", stage="draft",
                         purpose="DRAFT_CAMPAIGN", provider=label, model=spec_run.model, usage_in=record.tokens_in, usage_out=record.tokens_out,
                         stages=len(runs))
    return draft, record, {"mode": "staged", "reason": reason, "runs": [r.id for r in runs], "stages": len(runs)}
