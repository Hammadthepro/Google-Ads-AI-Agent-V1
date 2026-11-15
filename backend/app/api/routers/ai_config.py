"""Main AI / Challenger AI configuration and AI usage. Keys are write-only: no response ever contains one."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core import clock
from app.core.errors import NotFound
from app.db.session import get_db
from app.models import AIRun, Client, User
from app.services import ai_roles

router = APIRouter(tags=["ai"])


def _role(role: str) -> str:
    if role not in ai_roles.ROLES:
        raise NotFound("Unknown AI role")
    return role


class RoleIn(BaseModel):
    provider: str | None = None
    model: str | None = None
    api_key: str | None = None        # write-only; blank keeps the stored key (same provider only)
    enabled: bool | None = None
    pricing: dict | None = None       # optional USD per 1M tokens {input, output}
    review_policy: dict | None = None  # challenger only
    caps: dict | None = None           # application token caps (token_caps.FIELDS); empty value = no cap


class ProbeIn(BaseModel):
    provider: str | None = None
    model: str | None = None
    api_key: str | None = None        # test an unsaved key; it is used for this request only and never stored or returned
    refresh: bool = False


@router.get("/ai/roles")
def roles(user: User = Depends(require("reports.view")), db: Session = Depends(get_db)):
    out = ai_roles.overview(db)
    db.commit()  # persists a first-time adoption of the existing Groq settings
    return out


@router.put("/ai/roles/{role}")
def save_role(role: str, body: RoleIn, user: User = Depends(require("integrations.manage")), db: Session = Depends(get_db)):
    row = ai_roles.save(db, _role(role), body.model_dump(exclude_unset=True), user)
    db.commit()
    return ai_roles.serialize(db, row, role)


@router.post("/ai/roles/{role}/test")
def test_role(role: str, body: ProbeIn, user: User = Depends(require("integrations.manage")), db: Session = Depends(get_db)):
    out = ai_roles.test(db, _role(role), body.model_dump(exclude_unset=True), user)
    db.commit()
    return {**out, "role": ai_roles.serialize(db, ai_roles._row(db, role), role)}


@router.post("/ai/roles/{role}/models")
def discover_models(role: str, body: ProbeIn, user: User = Depends(require("integrations.manage")), db: Session = Depends(get_db)):
    out = ai_roles.discover(db, _role(role), body.model_dump(exclude_unset=True))
    db.commit()
    return out


@router.post("/ai/roles/{role}/revoke")
def revoke_role_key(role: str, user: User = Depends(require("integrations.manage")), db: Session = Depends(get_db)):
    row = ai_roles.revoke(db, _role(role), user)
    db.commit()
    return ai_roles.serialize(db, row, role)


@router.get("/ai/usage")
def usage(days: int = 30, user: User = Depends(require("integrations.manage")), db: Session = Depends(get_db)):
    """What each role, provider and model consumed. Tokens and latency come from provider responses; cost only where it is known
    (provider-reported, or from a price an admin entered). Assembled-draft records are summaries of stage calls and are not counted."""
    days = max(1, min(days, 365))
    since = clock.real_now() - timedelta(days=days)
    rows = db.scalars(select(AIRun).where(AIRun.created_at >= since)).all()
    groups: dict[tuple, dict] = {}
    by_task: dict[str, dict] = {}
    by_client: dict[str, dict] = {}
    by_day: dict[str, dict] = {}
    by_month: dict[str, dict] = {}

    def _add(bucket: dict, key: str, r: AIRun, **extra) -> None:
        b = bucket.setdefault(key, {**extra, "requests": 0, "input_tokens": 0, "output_tokens": 0, "failed": 0, "by_role": {}})
        b["requests"] += 1
        b["input_tokens"] += r.tokens_in or 0
        b["output_tokens"] += r.tokens_out or 0
        b["failed"] += 0 if r.validation_ok else 1
        role = r.role or "main"
        b["by_role"][role] = b["by_role"].get(role, 0) + (r.tokens_in or 0) + (r.tokens_out or 0)
    names = {c.id: c.name for c in db.scalars(select(Client))}
    for r in rows:
        if r.provider_status == "assembled":
            continue
        _add(by_client, r.client_id or "", r, client_id=r.client_id, client=names.get(r.client_id, "") if r.client_id else "Not tied to a client")
        _add(by_day, r.created_at.strftime("%Y-%m-%d"), r, day=r.created_at.strftime("%Y-%m-%d"))
        _add(by_month, r.created_at.strftime("%Y-%m"), r, month=r.created_at.strftime("%Y-%m"))
        key = (r.role or "main", r.provider, r.model or "—")
        g = groups.setdefault(key, {"role": key[0], "provider": key[1], "model": key[2], "requests": 0, "ok": 0, "failed": 0, "not_sent": 0,
                                    "input_tokens": 0, "output_tokens": 0, "latency_ms": 0, "cost_usd": 0.0, "cost_known": 0,
                                    "errors": {}, "last_at": None})
        g["requests"] += 1
        sent = r.provider_status != "not_sent"
        g["not_sent"] += 0 if sent else 1
        g["ok" if r.validation_ok else "failed"] += 1
        g["input_tokens"] += r.tokens_in or 0
        g["output_tokens"] += r.tokens_out or 0
        g["latency_ms"] += r.latency_ms or 0
        if r.cost_usd is not None:
            g["cost_usd"] += r.cost_usd
            g["cost_known"] += 1
        if r.error_kind:
            g["errors"][r.error_kind] = g["errors"].get(r.error_kind, 0) + 1
        at = r.created_at.isoformat()
        g["last_at"] = max(g["last_at"] or at, at)
        t = by_task.setdefault(r.purpose, {"task": r.purpose, "requests": 0, "input_tokens": 0, "output_tokens": 0, "failed": 0})
        t["requests"] += 1
        t["input_tokens"] += r.tokens_in or 0
        t["output_tokens"] += r.tokens_out or 0
        t["failed"] += 0 if r.validation_ok else 1
    out = []
    for g in groups.values():
        n = g["requests"] - g["not_sent"]
        out.append({**g, "total_tokens": g["input_tokens"] + g["output_tokens"], "avg_latency_ms": round(g["latency_ms"] / n) if n else None,
                    "cost_usd": round(g["cost_usd"], 4) if g["cost_known"] else None,
                    "cost_note": ("" if g["cost_known"] == g["requests"] else
                                  "no price known" if not g["cost_known"] else f"known for {g['cost_known']} of {g['requests']} requests")})
    out.sort(key=lambda g: -g["total_tokens"])
    return {"days": days, "since": since.isoformat(), "models": out,
            "tasks": sorted(by_task.values(), key=lambda t: -(t["input_tokens"] + t["output_tokens"])),
            "clients": sorted(by_client.values(), key=lambda t: -(t["input_tokens"] + t["output_tokens"])),
            "daily": sorted(by_day.values(), key=lambda t: t["day"]), "monthly": sorted(by_month.values(), key=lambda t: t["month"]),
            "note": "Tokens as reported by each provider. Requests refused by an application cap or the per-minute budget were not sent and used no tokens.",
            "totals": {"requests": sum(g["requests"] for g in out), "input_tokens": sum(g["input_tokens"] for g in out),
                       "output_tokens": sum(g["output_tokens"] for g in out)}}
