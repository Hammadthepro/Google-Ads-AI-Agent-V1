"""The conversational agent: one persistent thread per client that understands a request, decides what must run, refresh or be skipped
against the intelligence record, carries it out through the existing real services, and answers with evidence.

Design rules
* The agent owns NO knowledge. What it knows is the intelligence record (IntelligenceRun) plus the audit, feasibility and monitoring tables.
* Understanding is rules first (deterministic, labelled ``rules``); the AI is consulted only when the rules cannot decide (labelled ``ai``);
  if neither can, the agent asks. The AI never chooses an action outside the registered intents and never executes anything.
* Every request becomes a plan of stages, each decided ``run`` / ``reuse`` / ``skip`` with a reason, saved as it progresses so an
  interrupted run resumes from the first unfinished stage. Valid research is never repeated.
* The agent only reads and researches. Anything consequential (creating a draft that asks for approval, requesting approval of a
  recommendation) needs an explicit confirmation from a person with permission, and even then only an approval REQUEST is created:
  execution stays with the existing approval, write-guard and kill-switch machinery.
* No data is invented: unavailable stays unavailable, blockers name the exact prerequisite.
"""
from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.adapters import registry
from app.core import clock
from app.core.errors import AppError, Conflict, Forbidden, IntegrationNotConfigured, NotFound, ValidationFailed
from app.db.session import SessionLocal
from app.models import (AgentConversation, AgentMessage, AgentRun, Audit, Business, Client, FeasibilityResult, User)
from app.services import (accounts_service, activity, ai_service, audit_report, auth_service, clients_service, feasibility, intelligence_service as intel,
                          settings_service)

log = logging.getLogger("gaa.agent")
STALE_RUN_MINUTES = 15
OPEN_STATUSES = ("PLANNED", "RUNNING")


class Blocked(Exception):
    """A stage cannot run because of a named prerequisite. Recorded as BLOCKED with the exact reason; the run continues with what exists."""
    def __init__(self, message: str, prerequisite: str = ""):
        super().__init__(message)
        self.message, self.prerequisite = message, prerequisite


# ------------------------------------------------------------------------------------------------ registries (phase 2 plugs in here)
INTENTS: dict[str, dict] = {}
STEPS: dict[str, Callable] = {}


def intent(key: str, label: str, patterns: list[tuple[str, int]], plan: Callable, *, needs: tuple[str, ...] = ("client",), example: str = "",
           permission: str = "audits.run", confirm: bool = False):
    INTENTS[key] = {"key": key, "label": label, "patterns": [(re.compile(p, re.I), w) for p, w in patterns], "plan": plan, "needs": needs,
                    "example": example, "permission": permission, "confirm": confirm}


def step(key: str):
    def deco(fn):
        STEPS[key] = fn
        return fn
    return deco


class Ctx:
    """What a stage may use. Stages must be idempotent: a resumed run replays from the first unfinished stage."""
    def __init__(self, db: Session, run: AgentRun, conv: AgentConversation, user: User):
        self.db, self.run, self.conv, self.user = db, run, conv, user
        self.client = db.get(Client, run.client_id) if run.client_id else None
        bid = (run.params or {}).get("business_id") or (conv.context or {}).get("business_id")
        self.business = db.get(Business, bid) if bid else (self.client.businesses[0] if self.client and len(self.client.businesses) == 1 else None)
        self.params = run.params or {}
        self.out: dict = {}


# ------------------------------------------------------------------------------------------------ conversations
def _own(db: Session, user: User, conv_id: str) -> AgentConversation:
    c = db.get(AgentConversation, conv_id)
    if not c or (c.user_id != user.id and user.role != "SUPER_ADMIN"):
        raise NotFound("Conversation not found")
    if c.client_id:
        auth_service.require_client_access(db, user, c.client_id)
    return c


def open_conversation(db: Session, user: User, client_id: str | None, *, new: bool = False) -> AgentConversation:
    """One persistent thread per user and client: opening again returns the same thread with its history and remembered context."""
    if client_id:
        clients_service.get_client(db, user, client_id)  # existence + access
        if not new:
            c = db.scalar(select(AgentConversation).where(AgentConversation.user_id == user.id, AgentConversation.client_id == client_id,
                                                          AgentConversation.archived.is_(False)).order_by(AgentConversation.created_at.desc()))
            if c:
                return c
    cl = db.get(Client, client_id) if client_id else None
    c = AgentConversation(client_id=client_id, user_id=user.id, title=cl.name if cl else "New conversation", context={})
    db.add(c)
    db.flush()
    _say(db, c, "agent", "text", greeting(cl), {"suggestions": suggestions(cl)})
    return c


def greeting(cl: Client | None) -> str:
    if cl is None:
        return "Tell me which client you want to work on, or just ask: I will work out which stages need to run."
    return (f"I am working on {cl.name} ({cl.client_code}). I read what is already known, run only what is missing or out of date, "
            "and show the evidence. Nothing is changed in Google Ads without your explicit approval.")


def suggestions(cl: Client | None) -> list[str]:
    return ["Audit this client's business and website", "Find profitable keywords for these services", "Recalculate the recommended budget",
            "Prepare a campaign strategy using the latest research", "What is out of date?"] + (
        ["Why is this campaign underperforming?", "Show wasted spend and search terms", "What should we improve?"] if cl else [])


def _say(db: Session, conv: AgentConversation, role: str, kind: str, content: str, data: dict | None = None, run_id: str | None = None) -> AgentMessage:
    m = AgentMessage(conversation_id=conv.id, role=role, kind=kind, content=content, data=data or {}, run_id=run_id)
    db.add(m)
    db.flush()
    return m


# ------------------------------------------------------------------------------------------------ understanding
def score_intents(text: str) -> list[tuple[str, int]]:
    t = text.lower()
    scores = []
    for k, spec in INTENTS.items():
        s = sum(w for rx, w in spec["patterns"] if rx.search(t))
        if s:
            scores.append((k, s))
    return sorted(scores, key=lambda x: -x[1])


def interpret(db: Session, text: str) -> dict:
    """{intent, by, alternatives}. Rules decide when one intent clearly leads; the AI is asked only for ties or no match."""
    scores = score_intents(text)
    if scores and (len(scores) == 1 or scores[0][1] > scores[1][1]):
        return {"intent": scores[0][0], "by": "rules", "alternatives": [k for k, _ in scores[1:3]]}
    candidates = [k for k, s in scores if s == scores[0][1]] if scores else list(INTENTS)
    try:
        parsed, _ = ai_service.generate(db, "AGENT_INTENT", {"request": text[:1000], "allowed_intents": {k: INTENTS[k]["label"] for k in candidates}})
        if parsed.intent in INTENTS:
            return {"intent": parsed.intent, "by": "ai", "alternatives": []}
    except (IntegrationNotConfigured, ai_service.AIOutputInvalid):
        pass  # no AI opinion: fall through to asking, never to guessing
    return {"intent": None, "by": "none", "alternatives": candidates[:4]}


def _match_client(db: Session, user: User, text: str) -> tuple[list[Client], list[Client]]:
    """(matches in the text, all accessible)"""
    allc = clients_service.list_clients(db, user)
    t = text.lower()
    hits = [c for c in allc if c.client_code.lower() in t or (len(c.name) > 3 and c.name.lower() in t)]
    return hits, allc


# ------------------------------------------------------------------------------------------------ the conversation entry point
def post_message(db: Session, user: User, conv_id: str, text: str, *, choice: dict | None = None) -> tuple[AgentMessage, AgentRun | None]:
    conv = _own(db, user, conv_id)
    text = (text or "").strip()
    if not text and not choice:
        raise ValidationFailed("Write a request")
    if len(text) > 2000:
        raise ValidationFailed("Keep the request under 2000 characters")
    busy = db.scalar(select(AgentRun).where(AgentRun.conversation_id == conv.id, AgentRun.status.in_(OPEN_STATUSES)))
    if busy and not _is_stale(busy):
        raise Conflict("The agent is still working on the previous request. Wait for it to finish, or resume it if it was interrupted.")
    um = _say(db, conv, "user", "text", text or f"(selected: {choice.get('label') or choice.get('value')})")
    pending = (conv.context or {}).get("pending")
    original_text, forced_intent = text, None
    if pending and pending.get("kind") == "clarify":
        picked = _resolve_choice(pending, choice, text)
        if picked is not None:
            conv.context = {**conv.context, **{k: v for k, v in picked["context"].items()}, "pending": None}
            flag_modified(conv, "context")
            original_text, forced_intent = pending["original_text"], pending.get("intent")
            if pending["type"] == "intent":
                forced_intent = picked["value"]
            if pending["type"] == "client":
                conv.client_id = picked["value"]
                conv.title = db.get(Client, conv.client_id).name
        else:
            conv.context = {**conv.context, "pending": None}  # a new request replaces an unanswered question
            flag_modified(conv, "context")
    elif pending and pending.get("kind") == "confirm":
        conv.context = {**conv.context, "pending": None}
        flag_modified(conv, "context")
    # --- understand
    if forced_intent:
        reading = {"intent": forced_intent, "by": "selection", "alternatives": []}
    else:
        reading = interpret(db, original_text)
    if reading["intent"] is None:
        return _ask(db, conv, user, original_text, None, "intent", "I am not sure what you would like me to do. Which of these is closest?",
                    [{"label": INTENTS[k]["label"], "value": k, "context": {}} for k in reading["alternatives"]], um)
    spec = INTENTS[reading["intent"]]
    # --- who is it about
    if conv.client_id is None:
        hits, allc = _match_client(db, user, original_text)
        if len(hits) == 1:
            conv.client_id, conv.title = hits[0].id, hits[0].name
        else:
            pool = hits or allc[:8]
            if not pool:
                return _answer_text(db, conv, "There are no clients you can access yet. Create one first.", um)
            return _ask(db, conv, user, original_text, reading["intent"], "client", "Which client is this about?",
                        [{"label": f"{c.name} ({c.client_code})", "value": c.id, "context": {}} for c in pool], um)
    client = db.get(Client, conv.client_id)
    auth_service.require_client_access(db, user, client.id)
    if not auth_service.has_permission(user, spec["permission"]):
        raise Forbidden(f"Your role cannot ask for “{spec['label']}”")
    # --- which business
    bid = (conv.context or {}).get("business_id")
    if not bid or db.get(Business, bid) is None or db.get(Business, bid).client_id != client.id:
        if len(client.businesses) == 1:
            bid = client.businesses[0].id
        elif len(client.businesses) > 1:
            named = [b for b in client.businesses if b.name.lower() in original_text.lower()]
            if len(named) == 1:
                bid = named[0].id
            else:
                return _ask(db, conv, user, original_text, reading["intent"], "business", f"{client.name} has several businesses. Which one?",
                            [{"label": b.name, "value": b.id, "context": {"business_id": b.id}} for b in client.businesses], um)
        else:
            return _answer_text(db, conv, "This client has no business profile yet, so there is nothing to research. Edit the client to add one.", um)
        conv.context = {**(conv.context or {}), "business_id": bid}
        flag_modified(conv, "context")
    # --- keyword research never starts new external research by itself: ask whether to reuse what exists or run it fresh
    choice = (conv.context or {}).get("research_choice")
    if reading["intent"] == "keyword_research" and choice is None:
        ra, rr = research_decision(db, db.get(Business, bid), client, False)
        if ra == "run":
            from app.services import audit_jobs
            src = audit_jobs.keyword_sources(db, db.get(Business, bid))
            have = [x["label"] for x in src["sources"] if x["state"] in ("usable", "outdated")]
            return _ask(db, conv, user, original_text, reading["intent"], "research_choice",
                        f"Before keyword research: {rr}. {src['summary']} Use the research that already exists"
                        + (f" ({', '.join(have)})" if have else " (some sources are missing, so results may be incomplete)")
                        + ", or run fresh website, Business Profile and competitor research first? Nothing starts until you choose.",
                        [{"label": "Use the available data", "value": "reuse", "context": {"research_choice": "reuse"}},
                         {"label": "Run fresh research first (website, Business Profile, competitors)", "value": "fresh",
                          "context": {"research_choice": "fresh"}}], um)
    if choice is not None:
        conv.context = {k: v for k, v in (conv.context or {}).items() if k != "research_choice"}  # one answer covers one request
        flag_modified(conv, "context")
    run = AgentRun(conversation_id=conv.id, client_id=client.id, user_id=user.id, request_text=original_text, intent=reading["intent"],
                   interpreted_by=reading["by"], params={"business_id": bid, "alternatives": reading["alternatives"],
                                                         **({"research_choice": choice} if choice else {})}, status="PLANNED",
                   heartbeat_at=clock.now())
    db.add(run)
    db.flush()
    c = Ctx(db, run, conv, user)
    try:
        spec_plan = spec["plan"](c)
    except Blocked as b:
        run.status, run.error, run.finished_at = "COMPLETED_WITH_BLOCKERS", b.message, clock.now()
        run.result = {"headline": b.message, "blockers": [{"what": b.message, "needs": b.prerequisite}], "sections": [], "next_actions": []}
        am = _say(db, conv, "agent", "result", b.message, run.result, run.id)
        return am, run
    run.plan = [{"status": "PENDING" if s["action"] == "run" else "DONE", "result": s.get("reason", "") if s["action"] != "run" else "", **s} for s in spec_plan]
    interp = {"rules": "I read this as", "ai": "I read this (with AI help) as", "selection": "As you chose,"}[reading["by"]]
    plan_txt = f"{interp} “{spec['label']}”. Plan: " + "; ".join(f"{s['label']} — {s['action'].upper()}" for s in run.plan)
    _say(db, conv, "agent", "plan", plan_txt, {"plan": run.plan, "intent": run.intent, "interpreted_by": reading["by"]}, run.id)
    activity.log(db, "agent.request", actor=user, entity="agent_run", entity_id=run.id, client_id=client.id,
                 details={"intent": run.intent, "by": reading["by"], "plan": [(s["key"], s["action"]) for s in run.plan]})
    return um, run


def _is_stale(run: AgentRun) -> bool:
    return run.status in OPEN_STATUSES and run.heartbeat_at is not None and clock.now() - run.heartbeat_at > timedelta(minutes=STALE_RUN_MINUTES)


def _ask(db, conv, user, original_text, intent_key, typ, question, options, um) -> tuple[AgentMessage, None]:
    conv.context = {**(conv.context or {}), "pending": {"kind": "clarify", "type": typ, "intent": intent_key, "original_text": original_text, "options": options}}
    flag_modified(conv, "context")
    m = _say(db, conv, "agent", "clarification", question, {"options": [{"label": o["label"], "value": o["value"], "type": typ} for o in options], "type": typ})
    return m, None


def _answer_text(db, conv, text, um):
    return _say(db, conv, "agent", "text", text), None


def _resolve_choice(pending: dict, choice: dict | None, text: str) -> dict | None:
    opts = pending.get("options") or []
    if choice and choice.get("value") is not None:
        return next((o for o in opts if o["value"] == choice["value"]), None)
    t = text.lower().strip()
    if t.isdigit() and 1 <= int(t) <= len(opts):
        return opts[int(t) - 1]
    hit = [o for o in opts if t and (t == o["label"].lower() or t in o["label"].lower())]
    return hit[0] if len(hit) == 1 else None


# ------------------------------------------------------------------------------------------------ execution
def execute(run_id: str) -> str:
    """Run (or resume) a request in its own session. Safe to call again after an interruption."""
    db = SessionLocal()
    try:
        run = db.get(AgentRun, run_id)
        if run is None or run.status in ("COMPLETED", "COMPLETED_WITH_BLOCKERS", "CANCELLED", "WAITING_FOR_CONFIRMATION", "WAITING_FOR_CLARIFICATION"):
            return run.status if run else "MISSING"
        conv, user = db.get(AgentConversation, run.conversation_id), db.get(User, run.user_id)
        c = Ctx(db, run, conv, user)
        run.status, run.error, run.heartbeat_at = "RUNNING", "", clock.now()
        db.commit()
        blocked = False
        try:
            for i, st in enumerate(run.plan):
                if st["status"] == "DONE":
                    continue
                st = _set(run, i, status="RUNNING", started_at=clock.now().isoformat())
                db.commit()
                try:
                    res = STEPS[st["key"]](c) or {}
                    _set(run, i, status="DONE", result=res.get("summary", ""), finished_at=clock.now().isoformat())
                except Blocked as b:
                    blocked = True
                    _set(run, i, status="BLOCKED", result=b.message, prerequisite=b.prerequisite, finished_at=clock.now().isoformat())
                run.heartbeat_at = clock.now()
                db.commit()
            answer = STEPS["__answer__"](c)
            run.result = answer
            run.status = "WAITING_FOR_CONFIRMATION" if answer.get("confirmation") else ("COMPLETED_WITH_BLOCKERS" if (blocked or answer.get("blockers")) else "COMPLETED")
            if answer.get("confirmation"):
                conv.context = {**(conv.context or {}), "pending": {"kind": "confirm", "run_id": run.id, **answer["confirmation"]}}
                flag_modified(conv, "context")
            run.finished_at = clock.now()
            _say(db, conv, "agent", "confirmation" if answer.get("confirmation") else "result", answer["headline"], answer, run.id)
        except AppError as exc:
            _fail(db, run, conv, f"{exc.message}")
        except Exception as exc:  # noqa: BLE001 - recorded; the completed stages stay completed, so Resume continues after them
            log.exception("agent run %s failed", run.id)
            _fail(db, run, conv, f"{type(exc).__name__}: {str(exc)[:300]}")
        activity.log(db, "agent.run_finished", actor=user, entity="agent_run", entity_id=run.id, client_id=run.client_id,
                     details={"intent": run.intent, "status": run.status})
        db.commit()
        return run.status
    finally:
        db.close()


def _set(run: AgentRun, i: int, **kv) -> dict:
    plan = [dict(s) for s in run.plan]
    plan[i].update(kv)
    run.plan = plan
    flag_modified(run, "plan")
    return plan[i]


def _fail(db: Session, run: AgentRun, conv: AgentConversation, message: str) -> None:
    db.rollback()
    run = db.get(AgentRun, run.id)
    run.status, run.error, run.finished_at = "FAILED", message, clock.now()
    for i, s in enumerate(run.plan):
        if s["status"] == "RUNNING":
            _set(run, i, status="FAILED", result=message)
    _say(db, db.get(AgentConversation, run.conversation_id), "agent", "error",
         f"I could not finish: {message}. What already completed is kept; you can resume from where it stopped.", {"run_id": run.id}, run.id)


def dispatch(run_id: str, *, sync: bool = False) -> None:
    if sync:
        execute(run_id)
    else:
        threading.Thread(target=execute, args=(run_id,), daemon=True).start()


def resume(db: Session, user: User, run_id: str) -> AgentRun:
    run = db.get(AgentRun, run_id)
    if not run:
        raise NotFound("Run not found")
    _own(db, user, run.conversation_id)
    if _is_stale(run):
        run.status = "INTERRUPTED"
    if run.status not in ("INTERRUPTED", "FAILED"):
        raise Conflict(f"This run is {run.status.lower().replace('_', ' ')}; only interrupted or failed runs can be resumed")
    for i, s in enumerate(run.plan):
        if s["status"] in ("RUNNING", "FAILED", "BLOCKED"):
            _set(run, i, status="PENDING", result="")
    run.status = "PLANNED"
    activity.log(db, "agent.resume", actor=user, entity="agent_run", entity_id=run.id, client_id=run.client_id, details={"intent": run.intent})
    return run


def sweep_interrupted(db: Session) -> int:
    """Runs whose worker died (no heartbeat) become INTERRUPTED so they can be resumed instead of looking busy forever."""
    n = 0
    for r in db.scalars(select(AgentRun).where(AgentRun.status.in_(OPEN_STATUSES))):
        if _is_stale(r):
            r.status = "INTERRUPTED"
            n += 1
    return n


def confirm(db: Session, user: User, run_id: str, approve: bool) -> dict:
    """The explicit human confirmation. Approving creates ONLY what the confirmation described (a task or an approval request)."""
    run = db.get(AgentRun, run_id)
    if not run:
        raise NotFound("Run not found")
    conv = _own(db, user, run.conversation_id)
    pend = (conv.context or {}).get("pending") or {}
    if run.status != "WAITING_FOR_CONFIRMATION" or pend.get("run_id") != run.id:
        raise Conflict("Nothing is waiting for confirmation on this request")
    conv.context = {**conv.context, "pending": None}
    flag_modified(conv, "context")
    if not approve:
        run.status, run.finished_at = "CANCELLED", clock.now()
        _say(db, conv, "agent", "text", "Cancelled. Nothing was created.", {}, run.id)
        return {"status": "CANCELLED"}
    auth_service.require(user, pend["permission"])
    out = ACTIONS[pend["action"]](db, user, run, pend)
    run.status, run.finished_at = "COMPLETED", clock.now()
    run.result = {**run.result, "confirmed": out}
    flag_modified(run, "result")
    activity.log(db, "agent.confirmed", actor=user, entity="agent_run", entity_id=run.id, client_id=run.client_id, details={"action": pend["action"], **out})
    _say(db, conv, "agent", "result", out["message"], {"confirmed": out}, run.id)
    return out


ACTIONS: dict[str, Callable] = {}


# ------------------------------------------------------------------------------------------------ shared planning helpers
def research_decision(db: Session, biz: Business, client: Client, force: bool = False) -> tuple[str, str]:
    st = intel.research_status(db, biz, client)
    max_age = int(settings_service.get_value("agent.research_max_age_days", 30, db))
    if force:
        return "run", "You asked for fresh research"
    if not st["exists"]:
        return "run", "No completed research exists yet"
    if st["client_info_changed"]:
        return "run", "The client's information changed since the last research"
    if st["age_days"] > max_age:
        return "run", f"The last research is {st['age_days']} days old (limit {max_age})"
    return "reuse", f"Research from {st['date'][:10]} (v{st['version']}) still matches the client's information"


def modules_decision(db: Session, biz: Business, client: Client, wanted: list[str], research_will_run: bool, cause: str = "new research") -> tuple[str, str, list[str]]:
    stale = set(intel.stale_modules(db, biz, client))
    todo = [m for m in wanted if m in stale or research_will_run]
    if not todo:
        return "reuse", "All of these stages are current: " + ", ".join(intel.MODULES[m]["title"] for m in wanted), []
    why = f"{cause} makes them out of date" if research_will_run else "their inputs changed or they have not run"
    return "run", f"{', '.join(intel.MODULES[m]['title'] for m in todo)}: {why}", todo


def _steps_for_research(c: Ctx, wanted: list[str], force: bool = False) -> list[dict]:
    choice = c.params.get("research_choice")
    ra, rr = research_decision(c.db, c.business, c.client, force or bool(c.params.get("force")) or choice == "fresh")
    if choice == "fresh":
        rr = "You chose fresh research"
    elif choice == "reuse" and ra == "run":
        ra, rr = "skip", f"You chose to use the available data ({rr[0].lower() + rr[1:]}); results may be incomplete"
    ma, mr, todo = modules_decision(c.db, c.business, c.client, wanted, ra == "run")
    return [{"key": "research", "label": "External research (website, competitors, keywords)", "action": ra, "reason": rr},
            {"key": "modules", "label": "Update the intelligence record", "action": ma, "reason": mr, "modules": todo, "wanted": wanted}]


@step("research")
def _research(c: Ctx) -> dict:
    rep = audit_report.launch(c.db, c.business, c.client, c.user.id, sync=True)
    c.db.refresh(rep)
    if rep.status != "COMPLETED":
        raise AppError(f"The research run did not complete ({rep.status}): {rep.error or 'see the audit report'}")
    return {"summary": f"Research completed (report v{rep.version})"}


@step("modules")
def _modules(c: Ctx) -> dict:
    st = next(s for s in c.run.plan if s["key"] == "modules")
    todo = st.get("modules") or []
    if not todo:
        return {"summary": "Nothing to update"}
    out = intel.refresh(c.db, c.business, c.client, c.user.id, modules=todo)
    c.db.commit()
    return {"summary": f"Updated {len(out['ran'])} stage(s): " + ", ".join(out["ran"])}


@step("feasibility")
def _feasibility(c: Ctx) -> dict:
    ctx = intel.Ctx(c.db, c.business, c.client)
    audits = {t.lower(): _audit_result(c.db, c.business, t) for t in ("GBP", "WEBSITE")}
    history = None
    ads = registry.optional("google_ads")
    acct = accounts_service.account_for_business(c.db, c.business)
    if ads and acct:
        try:
            history = feasibility.account_history(ads, acct.customer_id)
        except Exception:  # noqa: BLE001 - history is optional input; its absence is stated in the result
            history = None
    r = feasibility.run(c.db, c.business, monthly_budget=float(c.business.monthly_budget_usd or 0), weekly_target=float(c.client.weekly_conversion_target or 0),
                        keyword_rows=ctx.kw_rows, audits=audits, account_history=history, source_type=(ads.source if ads else "NONE"))
    c.db.commit()
    s = r.summary
    return {"summary": f"Budget recalculated: ${s.get('recommended_monthly_budget') or s.get('monthly_budget'):,.0f}/month (basis: {r.inputs.get('basis')}, {r.inputs.get('budget_source')})"}


def _audit_result(db: Session, biz: Business, t: str) -> dict:
    row = db.scalar(select(Audit).where(Audit.business_id == biz.id, Audit.audit_type == t, Audit.status == "COMPLETED").order_by(Audit.created_at.desc()))
    return row.result if row else {}


def prerequisites(db: Session, *, keywords: bool = False) -> list[dict]:
    """Named blockers for integrations the request relies on. Exact missing prerequisite, never a vague failure."""
    out = []
    if keywords:
        try:
            registry.get("keyword_research")
        except IntegrationNotConfigured:
            from app.services import integrations_service
            for prov, what in (("google_ads", "the Google Ads authorisation"), ("keyword_planner", "the Keyword Planner research customer")):
                try:
                    integrations_service.ensure_configured(prov)
                except IntegrationNotConfigured as exc:
                    out.append({"what": f"Keyword Planner is not configured: {what} is missing, so no keyword volumes or bids can be retrieved.", "needs": exc.message + (f" Missing: {', '.join(exc.missing)}." if exc.missing else "")})
    return out


# ------------------------------------------------------------------------------------------------ answer composition
SEV = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def evidence_items(mod: dict, limit: int = 8) -> list[dict]:
    fs = [f for f in mod["findings"] if f["source_type"] != "none"]
    fs.sort(key=lambda f: ({"FAIL": 0, "WARN": 1, "UNAVAILABLE": 3}.get(f["result"], 2), SEV.get(f["severity"], 4)))
    return [{"finding_id": f["finding_id"], "check": f["category"], "text": f["finding"], "result": f["result"], "severity": f["severity"],
             "verification": f["verification"], "origin": f["origin"], "confidence": f["confidence"], "source": f["source_reference"] or f["source_type"],
             "retrieved_at": f["retrieved_at"], "recommendation": f["recommendation"], "limitations": f["limitations"]} for f in fs[:limit]]


def module_blockers(rep: dict, wanted: list[str]) -> list[dict]:
    out = []
    for m in rep["modules"]:
        if m["module"] not in wanted:
            continue
        if m["status"] in ("BLOCKED", "FAILED"):
            out.append({"what": f"{m['title']} is {m['status'].lower()}: {m['summary'] or 'a critical finding must be resolved'}", "needs": ""})
        if m["status"] == "WAITING_FOR_INTEGRATION":
            out.append({"what": f"{m['title']} is waiting for an integration or account.", "needs": m["summary"]})
        for f in m["findings"]:
            if f["result"] == "FAIL" and f["severity"] == "critical":
                out.append({"what": f["finding"], "needs": f["recommendation"], "finding_id": f["finding_id"]})
    return out


def base_answer(c: Ctx, wanted: list[str], headline_prefix: str) -> dict:
    rep = intel.report(c.db, c.business, c.client)
    plan = c.run.plan
    ran = [s["key"] for s in plan if s["action"] == "run" and s["status"] == "DONE"]
    reused = [s for s in plan if s["action"] == "reuse"]
    mods = {m["module"]: m for m in rep["modules"]}
    sections = [{"type": "findings", "title": f"{mods[m]['number']}. {mods[m]['title']}", "status": mods[m]["status"], "stale": mods[m]["stale"],
                 "assessed": f"{mods[m]['checks_evaluated']}/{mods[m]['checks_total']}", "items": evidence_items(mods[m])} for m in wanted]
    blockers = module_blockers(rep, wanted)
    for s in plan:
        if s["status"] == "BLOCKED":
            blockers.append({"what": s["result"], "needs": s.get("prerequisite", "")})
    warnings = []
    if rep["research"].get("exists") and not rep["research"]["up_to_date"]:
        warnings.append("The research behind these findings predates the client's latest information.")
    if not rep["account_linked"]:
        warnings.append("No Google Ads account is linked: account-based evidence is Unavailable (not zero). Research and strategy do not need one.")
    return {"headline": f"{headline_prefix} Readiness: {rep['decision_label']}.", "decision": rep["decision"], "decision_label": rep["decision_label"],
            "sections": sections, "blockers": blockers, "warnings": warnings, "ran": ran, "reused": [{"stage": s["label"], "why": s["reason"]} for s in reused],
            "stale_modules": rep["stale_modules"], "legend": rep["legend"]["note"], "next_actions": [], "account_linked": rep["account_linked"]}


@step("__answer__")
def _answer(c: Ctx) -> dict:
    return ANSWERS[c.run.intent](c)


ANSWERS: dict[str, Callable] = {}


def answer(key: str):
    def deco(fn):
        ANSWERS[key] = fn
        return fn
    return deco


# ------------------------------------------------------------------------------------------------ intents: research and strategy
WANT_AUDIT = ["business_intelligence", "website", "competitors"]
WANT_KEYWORDS = ["keywords", "search_intent"]
WANT_STRATEGY = intel.MODULE_KEYS


def plan_audit(c: Ctx) -> list[dict]:
    return _steps_for_research(c, WANT_AUDIT)


def plan_keywords(c: Ctx) -> list[dict]:
    return _steps_for_research(c, WANT_KEYWORDS)


def plan_budget(c: Ctx) -> list[dict]:
    steps = _steps_for_research(c, ["keywords", "geography"])
    steps.append({"key": "feasibility", "label": "Recalculate the recommended budget", "action": "run",
                  "reason": "You asked for a recalculation: it uses the latest keyword costs and the client's target"})
    steps.append({"key": "finance_modules", "label": "Update financial feasibility", "action": "run", "reason": "The budget result changes this stage"})
    return steps


@step("finance_modules")
def _finance_modules(c: Ctx) -> dict:
    out = intel.refresh(c.db, c.business, c.client, c.user.id, modules=["finance"])
    c.db.commit()
    return {"summary": "Updated: " + ", ".join(out["ran"])}


def plan_strategy(c: Ctx) -> list[dict]:
    return _steps_for_research(c, WANT_STRATEGY)


def plan_status(c: Ctx) -> list[dict]:
    return []


def plan_draft(c: Ctx) -> list[dict]:
    steps = _steps_for_research(c, WANT_STRATEGY)
    steps.append({"key": "confirm_draft", "label": "Ask you to confirm before creating the draft task", "action": "run",
                  "reason": "Creating the draft asks the AI to write ads and creates an approval request, so it needs your confirmation"})
    return steps


@step("confirm_draft")
def _confirm_draft(c: Ctx) -> dict:
    return {"summary": "Waiting for your confirmation"}


intent("audit_business", "Audit the business and website", [(r"\baudit", 3), (r"research (this |the )?(new )?(client|business|website|competitor)", 3),
                                                              (r"analy[sz]e (this |the )?(new )?(business|website|site|competitors?)", 3), (r"\bcompetitors?\b", 1), (r"\bwebsite\b", 1)],
       plan_audit, example="Audit this new client's business and website")
intent("keyword_research", "Research keywords", [(r"keywords?", 3), (r"what (do|are) people (search|typing)", 2), (r"search volume", 2), (r"profitable", 1)],
       plan_keywords, example="Find profitable keywords for these services")
intent("recalculate_budget", "Recalculate the recommended budget", [(r"budget", 3), (r"recalculat", 2), (r"how much (should|to|do)", 2), (r"\bcpl\b|cost per lead", 1),
                                                                    (r"affordable", 1)], plan_budget, example="Recalculate the recommended budget")
intent("prepare_strategy", "Prepare the campaign strategy", [(r"strateg", 3), (r"campaign plan|plan (the |a )?campaign", 3), (r"\breadiness\b|ready to (launch|build)|are we ready", 3),
                                                              (r"latest research", 1)], plan_strategy, example="Prepare a campaign strategy using the latest research")
intent("status", "Show what is known and what is out of date", [(r"\bstatus\b", 3), (r"what (do we|is) (know|stale|out of date)", 3), (r"out of date|up to date|stale", 3),
                                                                (r"summary of (the )?research", 2), (r"what do we know", 3)], plan_status, example="What is out of date?")
intent("create_draft", "Create a campaign draft for approval", [(r"(create|build|generate|draft|make|prepare) (me )?(a |the )?(new )?(campaign )?draft", 4),
                                                                (r"(create|build|make|launch|set up) (a |the |me a )?(new )?campaign", 4)],
       plan_draft, example="Create a campaign draft", permission="tasks.run", confirm=True)


@answer("audit_business")
def ans_audit(c: Ctx) -> dict:
    n_run, n_reuse = len([x for x in c.run.plan if x["action"] == "run"]), len([x for x in c.run.plan if x["action"] == "reuse"])
    a = base_answer(c, WANT_AUDIT, f"Audit of {c.client.name}: ran {n_run} stage(s), reused {n_reuse}.")
    a["next_actions"] = [{"label": "Research keywords", "say": "Find profitable keywords for these services"}, {"label": "Prepare the campaign strategy", "say": "Prepare a campaign strategy using the latest research"}]
    return a


@answer("keyword_research")
def ans_keywords(c: Ctx) -> dict:
    a = base_answer(c, WANT_KEYWORDS, f"Keyword research for {c.client.name}.")
    a["blockers"] += prerequisites(c.db, keywords=True)
    ictx = intel.Ctx(c.db, c.business, c.client)
    rows = [r for r in ictx.kw_rows if r.get("selected")][:25]
    a["sections"].insert(0, {"type": "table", "title": f"Selected keywords ({len([r for r in ictx.kw_rows if r.get('selected')])} of {len(ictx.kw_rows)} researched)",
                             "columns": ["keyword", "service", "avg monthly searches", "top-of-page bid", "why selected"],
                             "rows": [[r["keyword"], r.get("service") or "—", "unavailable" if r.get("avg_monthly_searches") is None else r["avg_monthly_searches"],
                                       r.get("bid_range") if r.get("bid_range") not in (None, "n/a") else "unavailable", r.get("label_reason") or r.get("label")] for r in rows],
                             "note": "Keyword Planner estimates. 'Unavailable' means Google returned no value: it is not zero."})
    if not ictx.kw_rows:
        a["warnings"].append("No keyword results exist yet" + (": see the blocker above." if a["blockers"] else "."))
    a["next_actions"] = [{"label": "Recalculate the budget with these costs", "say": "Recalculate the recommended budget"}]
    return a


@answer("recalculate_budget")
def ans_budget(c: Ctx) -> dict:
    a = base_answer(c, ["finance"], f"Budget recalculated for {c.client.name}.")
    fr = c.db.scalar(select(FeasibilityResult).where(FeasibilityResult.business_id == c.business.id).order_by(FeasibilityResult.created_at.desc()))
    if fr:
        s, i = fr.summary, fr.inputs
        a["sections"].insert(0, {"type": "numbers", "title": "Recommended budget (estimate, not a promise)", "items": [
            {"label": "Monthly", "value": f"${(s.get('recommended_monthly_budget') or s.get('monthly_budget')):,.0f}"}, {"label": "Daily", "value": f"${s.get('daily_budget'):,.2f}"},
            {"label": "Expected leads / week", "value": s.get("expected_weekly_conversions")}, {"label": "Realistic range", "value": s.get("realistic_weekly_range")},
            {"label": "Allowed cost per lead", "value": s.get("allowed_cost_per_lead")}, {"label": "Basis", "value": f"{i.get('basis')} · budget {i.get('budget_source')}"}],
            "scenarios": fr.scenarios, "note": "Planning arithmetic from stated inputs. CPC comes from Keyword Planner bid estimates or account history; conversion rates are assumptions until measured."})
    else:
        a["blockers"].append({"what": "No budget could be calculated.", "needs": "Keyword cost estimates or account history"})
    a["blockers"] += prerequisites(c.db, keywords=True)
    return a


@answer("prepare_strategy")
def ans_strategy(c: Ctx) -> dict:
    a = base_answer(c, WANT_STRATEGY, f"Campaign strategy for {c.client.name}.")
    rep = intel.report(c.db, c.business, c.client)
    strat = next(m for m in rep["modules"] if m["module"] == "strategy")
    a["sections"] = [s for s in a["sections"] if s["title"].startswith(("10.", "7.", "5."))] + [s for s in a["sections"] if not s["title"].startswith(("10.", "7.", "5."))]
    a["conditions"] = next((f["value"].get("conditions", []) for f in strat["findings"] if f["category"] == "readiness_decision"), [])
    a["next_actions"] = [{"label": "Create a campaign draft for approval", "say": "Create a campaign draft"}]
    return a


@answer("status")
def ans_status(c: Ctx) -> dict:
    rep = intel.report(c.db, c.business, c.client)
    stale = [m for m in rep["modules"] if m["stale"]]
    a = {"headline": f"{c.client.name}: {len(rep['modules']) - len(stale)} of {len(rep['modules'])} stages current; readiness {rep['decision_label']}.",
         "decision": rep["decision"], "decision_label": rep["decision_label"],
         "sections": [{"type": "numbers", "title": "The ten stages", "items": [{"label": f"{m['number']}. {m['title']}", "value": f"{m['status'].replace('_', ' ').lower()}{' · OUT OF DATE' if m['stale'] and m['version'] else ''} · {m['checks_evaluated']}/{m['checks_total']} assessed"} for m in rep["modules"]]}],
         "blockers": [], "warnings": [], "ran": [], "reused": [], "stale_modules": rep["stale_modules"], "next_actions": [], "account_linked": rep["account_linked"],
         "research": rep["research"], "legend": rep["legend"]["note"]}
    if not rep["research"]["exists"]:
        a["warnings"].append("No research has been done yet.")
        a["next_actions"].append({"label": "Audit the business and website", "say": "Audit this client's business and website"})
    elif not rep["research"]["up_to_date"]:
        a["warnings"].append(rep["research"]["reason"])
        a["next_actions"].append({"label": "Refresh the research", "say": "Audit this client's business and website"})
    if not rep["account_linked"]:
        a["warnings"].append("No Google Ads account linked (needed only for account data and deployment).")
    return a


@answer("create_draft")
def ans_draft(c: Ctx) -> dict:
    a = base_answer(c, WANT_STRATEGY, f"Ready to create a campaign draft for {c.client.name}.")
    a["confirmation"] = {"action": "create_build_task", "permission": "tasks.run", "business_id": c.business.id,
                         "describes": ("This will create a “Build campaign” task: the AI drafts the campaign from the research above, it is validated, and an approval REQUEST is "
                                       "created for a person to review. Nothing is created in Google Ads, no money is spent, and campaign writes are disabled by configuration."
                                       + ("" if a["account_linked"] else " No Google Ads account is linked, so the draft cannot be deployed until one is.")),
                         "readiness": a["decision_label"]}
    return a


def _create_build_task(db: Session, user: User, run: AgentRun, pend: dict) -> dict:
    from app.services import tasks_service
    t = tasks_service.create(db, user, {"task_type": "BUILD_CAMPAIGN", "client_id": run.client_id, "business_id": pend["business_id"], "fields": {},
                                        "note": f"Requested through the agent: {run.request_text[:200]}"})
    db.commit()
    try:
        from app.api.routers.workflow import dispatch as dispatch_task
        dispatch_task(t.id)
    except Exception as exc:  # noqa: BLE001
        log.warning("task dispatch failed: %s", type(exc).__name__)
    return {"task_id": t.id, "message": f"Created task {t.id[:8]}… (Build campaign). It will draft, validate and ask for approval; follow it under Tasks. Nothing was sent to Google Ads."}


ACTIONS["create_build_task"] = _create_build_task


# ------------------------------------------------------------------------------------------------ serialisation
def serialize_message(m: AgentMessage) -> dict:
    return {"id": m.id, "role": m.role, "kind": m.kind, "content": m.content, "data": m.data, "run_id": m.run_id, "created_at": m.created_at.isoformat()}


def serialize_run(r: AgentRun) -> dict:
    return {"id": r.id, "intent": r.intent, "intent_label": INTENTS.get(r.intent, {}).get("label", r.intent), "interpreted_by": r.interpreted_by, "status": r.status,
            "plan": r.plan, "result": r.result, "error": r.error, "request_text": r.request_text, "started_at": r.started_at.isoformat(),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None, "resumable": r.status in ("INTERRUPTED", "FAILED") or _is_stale(r)}


def thread(db: Session, user: User, conv_id: str) -> dict:
    conv = _own(db, user, conv_id)
    sweep_interrupted(db)
    msgs = db.scalars(select(AgentMessage).where(AgentMessage.conversation_id == conv.id).order_by(AgentMessage.created_at, AgentMessage.id)).all()
    runs = db.scalars(select(AgentRun).where(AgentRun.conversation_id == conv.id).order_by(AgentRun.started_at)).all()
    pend = (conv.context or {}).get("pending")
    return {"id": conv.id, "client_id": conv.client_id, "title": conv.title, "context": {k: v for k, v in (conv.context or {}).items() if k != "pending"},
            "pending": ({"kind": pend["kind"], "type": pend.get("type"), "run_id": pend.get("run_id")} if pend else None),
            "messages": [serialize_message(m) for m in msgs], "runs": {r.id: serialize_run(r) for r in runs},
            "busy": any(r.status in OPEN_STATUSES for r in runs), "intents": [{"key": k, "label": v["label"], "example": v["example"]} for k, v in INTENTS.items()]}


# Monitoring and optimisation intents register themselves here (same planner, executor and record).
from app.services import agent_monitoring  # noqa: E402,F401
