"""Manual application token caps per AI role (Main AI, Challenger AI), enforced by the backend.

These are the app's OWN limits, set by an administrator. They are separate from what the provider enforces (tokens and requests per
minute, the model's context window and maximum output), which are reported next to them but never stored here.

Per request: max input tokens (estimated before sending; nothing over it is sent), max output tokens (the answer is capped at it),
max total tokens (input + output; the output is reduced to fit, or the request is refused when not even a minimal answer fits).
Budgets: tokens per task, per client per day and per client per month (UTC). A request reserves its worst case (input estimate +
output cap) in the ledger with one atomic UPDATE ... WHERE used + n <= cap; the reservation is settled to the real usage afterwards,
so concurrent tasks for one client cannot overspend. Over a cap, nothing is sent and the error says which cap and by how much.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.errors import ValidationFailed
from app.db.session import session_scope
from app.models import AITokenLedger
from app.core.util import new_id

FIELDS = {
    "max_input_tokens": "Max input tokens per request",
    "max_output_tokens": "Max output tokens per request",
    "max_total_tokens": "Max total tokens per request",
    "task_budget_tokens": "Token budget per task",
    "client_daily_tokens": "Tokens per client per day",
    "client_monthly_tokens": "Tokens per client per month",
}
MIN_OUTPUT = 256        # an answer smaller than this cannot hold a useful JSON result
HARD_MAX = 10_000_000   # sanity bound on any single cap


def validate(raw: dict | None, model_meta: dict | None = None) -> dict:
    """Clean caps from an admin form: whole numbers >= 1 or empty (no cap). Rejects contradictions and caps a model cannot honour."""
    out: dict[str, int] = {}
    for k, v in (raw or {}).items():
        if k not in FIELDS:
            raise ValidationFailed(f"Unknown token cap “{k}”")
        if v in (None, "", 0, "0"):
            continue
        try:
            n = int(str(v).replace(",", "").strip())
        except ValueError:
            raise ValidationFailed(f"{FIELDS[k]} must be a whole number of tokens") from None
        if n < 1 or n > HARD_MAX:
            raise ValidationFailed(f"{FIELDS[k]} must be between 1 and {HARD_MAX:,} tokens")
        out[k] = n
    meta = model_meta or {}
    if out.get("max_output_tokens") and out["max_output_tokens"] < MIN_OUTPUT:
        raise ValidationFailed(f"Max output tokens below {MIN_OUTPUT} cannot hold a complete answer")
    if meta.get("max_output") and out.get("max_output_tokens", 0) > int(meta["max_output"]):
        raise ValidationFailed(f"Max output tokens ({out['max_output_tokens']:,}) is above the model's own maximum output "
                               f"({int(meta['max_output']):,}); the provider would refuse it")
    if meta.get("context_window") and out.get("max_input_tokens", 0) > int(meta["context_window"]):
        raise ValidationFailed(f"Max input tokens ({out['max_input_tokens']:,}) is above the model's context window ({int(meta['context_window']):,})")
    total = out.get("max_total_tokens")
    if total and out.get("max_input_tokens") and out["max_input_tokens"] >= total:
        raise ValidationFailed("Max total tokens must be larger than max input tokens, or no answer could fit")
    for small, big in (("task_budget_tokens", "client_daily_tokens"), ("client_daily_tokens", "client_monthly_tokens")):
        if out.get(small) and out.get(big) and out[small] > out[big]:
            raise ValidationFailed(f"{FIELDS[small]} ({out[small]:,}) is larger than {FIELDS[big].lower()} ({out[big]:,})")
    per_request = total or ((out.get("max_input_tokens") or 0) + (out.get("max_output_tokens") or 0)) or None
    for k in ("task_budget_tokens", "client_daily_tokens", "client_monthly_tokens"):
        if per_request and out.get(k) and out[k] < per_request:
            raise ValidationFailed(f"{FIELDS[k]} ({out[k]:,}) is smaller than one request may use ({per_request:,}); every request would be refused")
    return out


@dataclass
class Plan:
    max_output: int | None                 # the output cap to send (None: provider/adapter default)
    reserve: int                           # worst case reserved in the budgets
    buckets: list[tuple[str, str, str, int]] = field(default_factory=list)  # (scope, key, period, cap)


class CapExceeded(Exception):
    def __init__(self, message: str, details: dict):
        super().__init__(message)
        self.message, self.details = message, details


def plan(caps: dict, *, estimate: int, requested_output: int | None, default_output: int | None, task_id: str | None,
         client_id: str | None, now: datetime | None = None) -> Plan:
    """Per-request checks. Raises CapExceeded before anything is sent; returns the output cap to use and the budgets to reserve."""
    caps = caps or {}
    if caps.get("max_input_tokens") and estimate > caps["max_input_tokens"]:
        raise CapExceeded(f"The request needs ~{estimate:,} input tokens; the application cap is {caps['max_input_tokens']:,} per request. Nothing was sent.",
                          {"cap": "max_input_tokens", "limit": caps["max_input_tokens"], "needed": estimate})
    out = requested_output or default_output
    if caps.get("max_output_tokens"):
        out = min(out or caps["max_output_tokens"], caps["max_output_tokens"])
    if caps.get("max_total_tokens"):
        room = caps["max_total_tokens"] - estimate
        if room < MIN_OUTPUT:
            raise CapExceeded(f"The request needs ~{estimate:,} input tokens, leaving {max(room, 0):,} of the {caps['max_total_tokens']:,}-token "
                              f"per-request cap for the answer (at least {MIN_OUTPUT} are needed). Nothing was sent.",
                              {"cap": "max_total_tokens", "limit": caps["max_total_tokens"], "needed": estimate + MIN_OUTPUT})
        out = min(out or room, room)
    now = now or datetime.now(timezone.utc)
    buckets = []
    if task_id and caps.get("task_budget_tokens"):
        buckets.append(("task", task_id, "all", caps["task_budget_tokens"]))
    if client_id and caps.get("client_daily_tokens"):
        buckets.append(("client_day", client_id, now.strftime("%Y-%m-%d"), caps["client_daily_tokens"]))
    if client_id and caps.get("client_monthly_tokens"):
        buckets.append(("client_month", client_id, now.strftime("%Y-%m"), caps["client_monthly_tokens"]))
    return Plan(max_output=out, reserve=estimate + (out or 0), buckets=buckets)


LABEL = {"task": "this task's token budget", "client_day": "the client's daily token budget", "client_month": "the client's monthly token budget"}


def reserve(workspace_id: str, role: str, p: Plan) -> list[tuple[str, str, str]]:
    """Reserve p.reserve tokens in every bucket, atomically per bucket; all or nothing. Uses its own short transaction so the reservation
    is visible to concurrent requests at once. Raises CapExceeded naming the budget."""
    taken: list[tuple[str, str, str]] = []
    try:
        for scope, key, period, cap in p.buckets:
            with session_scope() as db:
                db.execute(pg_insert(AITokenLedger).values(id=new_id(), workspace_id=workspace_id, role=role, scope=scope, scope_key=key,
                                                           period=period, used=0, created_at=func.now(), updated_at=func.now())
                           .on_conflict_do_nothing(constraint="uq_ai_token_ledger_bucket"))
                cond = and_(AITokenLedger.workspace_id == workspace_id, AITokenLedger.role == role, AITokenLedger.scope == scope,
                            AITokenLedger.scope_key == key, AITokenLedger.period == period)
                got = db.execute(update(AITokenLedger).where(cond, AITokenLedger.used + p.reserve <= cap)
                                 .values(used=AITokenLedger.used + p.reserve, updated_at=func.now()).returning(AITokenLedger.used)).first()
                if got is None:
                    used = db.scalar(select(AITokenLedger.used).where(cond)) or 0
                    raise CapExceeded(f"This request could use up to {p.reserve:,} tokens, and {LABEL[scope]} has {max(cap - used, 0):,} of "
                                      f"{cap:,} left ({period if period != 'all' else 'whole task'}). Nothing was sent. "
                                      "An administrator can raise the cap in the AI role settings.",
                                      {"cap": {"task": "task_budget_tokens", "client_day": "client_daily_tokens",
                                               "client_month": "client_monthly_tokens"}[scope], "limit": cap, "used": used, "needed": p.reserve})
            taken.append((scope, key, period))
    except CapExceeded:
        settle(workspace_id, role, taken, p.reserve, 0)
        raise
    return taken


def settle(workspace_id: str, role: str, taken: list[tuple[str, str, str]], reserved: int, actual: int) -> None:
    """Replace a reservation by the real usage (never below zero). Called after every request, sent or failed."""
    delta = int(actual) - int(reserved)
    if not taken or delta == 0:
        return
    with session_scope() as db:
        for scope, key, period in taken:
            db.execute(update(AITokenLedger).where(AITokenLedger.workspace_id == workspace_id, AITokenLedger.role == role,
                                                   AITokenLedger.scope == scope, AITokenLedger.scope_key == key, AITokenLedger.period == period)
                       .values(used=func.greatest(AITokenLedger.used + delta, 0), updated_at=func.now()))


def usage(workspace_id: str, role: str, client_id: str | None, task_id: str | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    want = {"client_day": (client_id, now.strftime("%Y-%m-%d")), "client_month": (client_id, now.strftime("%Y-%m")), "task": (task_id, "all")}
    out = {}
    with session_scope() as db:
        for scope, (key, period) in want.items():
            if key:
                out[scope] = db.scalar(select(AITokenLedger.used).where(AITokenLedger.workspace_id == workspace_id, AITokenLedger.role == role,
                                                                        AITokenLedger.scope == scope, AITokenLedger.scope_key == key,
                                                                        AITokenLedger.period == period)) or 0
    return out
