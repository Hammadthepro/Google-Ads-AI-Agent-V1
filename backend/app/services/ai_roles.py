"""Main AI and Challenger AI: which provider and model each role uses in a workspace, with its encrypted key.

- Main AI: does the work (research synthesis, planning, campaign drafting, diagnosis, ...). Required for AI features.
- Challenger AI: independently reviews important outputs (see REVIEWS). Optional. When it is off or not configured, nothing pretends
  a review happened: the output records "independent review not performed" and why.

Keys: write-only, Fernet-encrypted with the deployment's TOKEN_ENCRYPTION_KEY (the same mechanism as every other credential), never
returned, never logged; the API shows a fingerprint. A role may instead reference an existing integration's key (`credential_ref`),
which is how existing Groq settings are adopted without asking for the key again and without copying it.
Every row belongs to one workspace; reads and writes always go through the caller's workspace.
"""
from __future__ import annotations

import json
import time
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.ads_oauth import decrypt_token, encrypt_token
from app.adapters.ai import providers as P
from app.adapters.base import AdapterError
from app.core import clock
from app.core.errors import NotFound, ValidationFailed
from app.core.util import fingerprint
from app.db.session import session_scope
from app.models import AIRoleConfig
from app.services import activity, integrations_service

ROLES = {"main": "Main AI", "challenger": "Challenger AI"}
# What the Challenger reviews. Trivial or deterministic work (classification, labelling, formatting, calculations) is never sent to it.
REVIEWS = {
    "campaign_draft": {"label": "Campaign and ad group drafts", "default": True,
                       "detail": "Structure, keywords, targeting, budget assumptions, claims and landing pages, with the research it was built from."},
    "ad_copy": {"label": "New and edited ads", "default": True, "detail": "Claims, policy risk and relevance of ad text."},
    "performance_diagnosis": {"label": "Weekly performance diagnosis", "default": False,
                              "detail": "The AI's stated causes of a shortfall, checked against the metrics. Advisory only."},
}
MODELS_TTL = timedelta(hours=24)
TEST_PROMPT = 'Reply with exactly this JSON object and nothing else: {"ok": true}'


# --------------------------------------------------------------------------------------------- rows
def _rows(db: Session) -> dict[str, AIRoleConfig]:
    wid = integrations_service.workspace_id(db)
    return {r.role: r for r in db.scalars(select(AIRoleConfig).where(AIRoleConfig.workspace_id == wid))}


def _row(db: Session, role: str, create: bool = False) -> AIRoleConfig | None:
    if role not in ROLES:
        raise NotFound("Unknown AI role")
    bootstrap(db)
    row = _rows(db).get(role)
    if row is None and create:
        row = AIRoleConfig(workspace_id=integrations_service.workspace_id(db), role=role, model_meta={}, models_cache=[], last_test_steps=[],
                           pricing={}, review_policy={})
        db.add(row)
        db.flush()
    return row


def bootstrap(db: Session) -> list[str]:
    """Adopt the existing Groq integration as the Main AI (and its challenger model as the Challenger AI) for each role that has no row
    yet in this workspace. By reference only: the key stays where it is. A role that has a row (anything a person saved, or an earlier
    adoption) is never touched again."""
    existing = _rows(db)
    if "main" in existing and "challenger" in existing:
        return []
    groq = integrations_service.effective("groq", integrations_service._row(db, "groq"))
    if not (groq.get("api_key") and groq.get("model")):
        return []
    wid, made = integrations_service.workspace_id(db), []
    for role, model in (("main", groq["model"]), ("challenger", groq.get("challenger_model"))):
        if role in existing or not model:
            continue
        db.add(AIRoleConfig(workspace_id=wid, role=role, provider="groq", model=model, enabled=True, credential_ref="integration:groq",
                            source="migrated_from_groq", status="UNTESTED", model_meta={}, models_cache=[], last_test_steps=[], pricing={},
                            review_policy={}))
        made.append(role)
    if made:
        db.flush()
        activity.log(db, "ai_role.migrated", entity="ai_role", details={"roles": made, "from": "groq integration (by reference, key not copied)"})
    return made


def _key(row: AIRoleConfig | None) -> str:
    if row is None:
        return ""
    if row.secrets_ciphertext:
        try:
            return decrypt_token(row.secrets_ciphertext)
        except Exception:  # noqa: BLE001 - key rotated / damaged: treat as missing (reported as unreadable), never crash
            return ""
    if row.credential_ref == "integration:groq" and row.provider == "groq":
        return integrations_service.resolve("groq").get("api_key") or ""
    return ""


def _key_unreadable(row: AIRoleConfig) -> bool:
    if not row.secrets_ciphertext:
        return False
    try:
        decrypt_token(row.secrets_ciphertext)
        return False
    except Exception:  # noqa: BLE001
        return True


def review_policy(row: AIRoleConfig | None) -> dict:
    stored = (row.review_policy if row else {}) or {}
    return {k: bool(stored.get(k, v["default"])) for k, v in REVIEWS.items()}


# --------------------------------------------------------------------------------------------- runtime resolution
_cache: dict[str, tuple[float, dict]] = {}


def invalidate() -> None:
    integrations_service.invalidate()  # clears this module's cache too and resets the adapter registry


def resolve(role: str) -> dict:
    """What the adapter registry needs for a role (own short session, cached ~15 s, cleared on every change)."""
    ckey = f"{integrations_service._workspace_override.get() or 'default'}:{role}"
    hit = _cache.get(ckey)
    if hit and time.monotonic() - hit[0] < 15:
        return hit[1]
    with session_scope() as db:
        row = _row(db, role)
        out = {"role": role, "configured": False, "enabled": False, "reason": ""}
        if row is None or not row.provider:
            out["reason"] = f"{ROLES[role]} is not configured"
        else:
            key = _key(row)
            ref_disabled = bool(row.credential_ref == "integration:groq" and not row.secrets_ciphertext
                                and integrations_service.resolve("groq").get("_disabled"))
            if ref_disabled:  # a role that borrows the Groq integration's key is off when that integration is switched off
                key = ""
            out.update(provider=row.provider, model=row.model, enabled=bool(row.enabled), api_key=key, model_meta=row.model_meta or {},
                       pricing=row.pricing or None, review_policy=review_policy(row), ref_disabled=ref_disabled)
            out["configured"] = bool(key and row.model)
            out["reason"] = ("" if out["configured"] else
                             f"{ROLES[role]} uses the Groq integration's key, and that integration is disabled" if ref_disabled else
                             "the stored key cannot be decrypted (TOKEN_ENCRYPTION_KEY changed); enter the key again" if _key_unreadable(row) else
                             f"{ROLES[role]} has no API key" if not key else f"{ROLES[role]} has no model selected")
            if out["configured"] and not row.enabled:
                out["reason"] = f"{ROLES[role]} is turned off"
    _cache[ckey] = (time.monotonic(), out)
    return out


def build_provider(role: str):
    """The adapter for a role, or IntegrationNotConfigured (with the precise reason). Never another provider, never a stand-in."""
    from app.core.errors import IntegrationNotConfigured
    v = resolve(role)
    if not v["configured"] or not v["enabled"]:
        raise IntegrationNotConfigured(f"ai_{role}", ROLES[role], disabled=(v["configured"] and not v["enabled"]) or bool(v.get("ref_disabled")),
                                       missing=[] if v["configured"] else [v["reason"]])
    p = P.build(v["provider"], v["api_key"], v["model"], model_meta=v.get("model_meta"), pricing=v.get("pricing"))
    p.role = role
    return p


def _adapter(name: str):
    from app.adapters import registry
    from app.core.errors import IntegrationNotConfigured
    try:
        return registry.get(name), ""
    except IntegrationNotConfigured as exc:
        return None, exc.message


def _identity(p) -> tuple[str, str]:
    return (getattr(p, "provider_key", None) or getattr(p, "name", "") or "", getattr(p, "model", "") or "")


def challenger_status() -> dict:
    """Whether independent review can run (the registry is the single gate that builds the adapter) and whether it is genuinely
    independent of the Main AI."""
    c = resolve("challenger")
    chal, why = _adapter("challenger_ai")
    main, _ = _adapter("ai")
    ok = chal is not None
    (cp, cm), (mp, mm) = _identity(chal), _identity(main)
    same_provider = ok and main is not None and cp == mp
    same_model = same_provider and cm == mm
    return {"available": ok, "reason": "" if ok else (c.get("reason") or why), "provider": cp or c.get("provider"), "model": cm or c.get("model"),
            "same_provider": same_provider, "same_model": same_model, "review_policy": c.get("review_policy") or review_policy(None),
            "independence": ("none: the Challenger is the same provider and model as the Main AI" if same_model else
                             "limited: same provider as the Main AI, different model" if same_provider else
                             "independent provider" if ok else "not performed")}


def reviews(kind: str) -> bool:
    st = challenger_status()
    return bool(st["available"] and st["review_policy"].get(kind, False))


# --------------------------------------------------------------------------------------------- API helpers
def serialize(db: Session, row: AIRoleConfig | None, role: str) -> dict:
    spec = P.SPECS.get(row.provider) if row and row.provider else None
    key_source = ("saved" if row and row.secrets_ciphertext else
                  "groq_integration" if row and row.credential_ref == "integration:groq" and _key(row) else "")
    return {
        "role": role, "label": ROLES[role], "provider": row.provider if row else "", "provider_label": spec.label if spec else "",
        "model": row.model if row else "", "enabled": bool(row and row.enabled), "status": row.status if row else "NOT_CONFIGURED",
        "source": row.source if row else "",
        "key": {"present": bool(key_source), "source": key_source, "fingerprint": row.key_fingerprint if row and row.secrets_ciphertext else "",
                "unreadable": bool(row and _key_unreadable(row))},
        "model_meta": (row.model_meta if row else {}) or {},
        "models_fetched_at": row.models_fetched_at.isoformat() if row and row.models_fetched_at else None,
        "models_error": row.models_error if row else "",
        "last_test": {"at": row.last_test_at.isoformat() if row and row.last_test_at else None, "ok": row.last_test_ok if row else None,
                      "steps": (row.last_test_steps if row else []) or [], "error": row.last_error if row else "",
                      "error_kind": row.last_error_kind if row else ""},
        "pricing": (row.pricing if row else {}) or {},
        **({"review_policy": review_policy(row), "reviews": REVIEWS} if role == "challenger" else {}),
        "updated_at": row.updated_at.isoformat() if row else None,
    }


def overview(db: Session) -> dict:
    migrated = bootstrap(db)
    rows = _rows(db)
    main, chal = rows.get("main"), rows.get("challenger")
    same = bool(main and chal and main.provider and main.provider == chal.provider)
    return {"roles": {r: serialize(db, rows.get(r), r) for r in ROLES},
            "providers": [{"key": s.key, "label": s.label, "docs": s.docs, "key_hint": s.key_hint, "discovery": s.discovery,
                           "api_features": list(s.api_features), "console": s.console} for s in P.SPECS.values()],
            "independence": {"same_provider": same, "same_model": bool(same and main.model == chal.model),
                             "note": ("The Challenger uses the same provider and model as the Main AI: its review is not an independent second "
                                      "opinion." if same and main.model == chal.model else
                                      "Same provider, different model: limited independence (shared training and policies)." if same else "")},
            "migrated_now": migrated}


def _validate_pricing(pricing) -> dict:
    if not pricing:
        return {}
    out = {}
    for k in ("input", "output"):
        v = pricing.get(k)
        if v in (None, ""):
            continue
        try:
            f = float(v)
        except (TypeError, ValueError) as exc:
            raise ValidationFailed(f"Price per 1M {k} tokens must be a number") from exc
        if f < 0 or f > 10000:
            raise ValidationFailed(f"Price per 1M {k} tokens must be between 0 and 10000 USD")
        out[k] = f
    return out if len(out) == 2 else {}


def save(db: Session, role: str, body: dict, user) -> AIRoleConfig:
    """Write-only key: a blank key keeps the stored one, but ONLY for the same provider (a key is never sent to another provider)."""
    provider = (body.get("provider") or "").strip()
    if provider and provider not in P.SPECS:
        raise ValidationFailed("Unknown provider")
    row = _row(db, role, create=True)
    new_key = (body.get("api_key") or "").strip()
    changed = []
    if provider and provider != row.provider:
        changed.append("provider")
        row.provider = provider
        if not new_key:  # the old key belongs to the old provider
            row.secrets_ciphertext, row.key_fingerprint, row.credential_ref = "", "", ""
        row.models_cache, row.models_fetched_at, row.models_error, row.model_meta = [], None, "", {}
        if row.credential_ref and provider != "groq":
            row.credential_ref = ""
    if new_key:
        integrations_service._require_safe_encryption_key()
        if len(new_key) < 8 or any(c.isspace() for c in new_key):
            raise ValidationFailed("That does not look like an API key")
        row.secrets_ciphertext, row.key_fingerprint, row.credential_ref = encrypt_token(new_key), fingerprint(new_key), ""
        changed.append("api_key")
    if "model" in body:
        model = (body.get("model") or "").strip()
        if len(model) > 200:
            raise ValidationFailed("Model id is too long")
        if model != row.model:
            changed.append("model")
            row.model = model
            row.model_meta = next((m for m in (row.models_cache or []) if m.get("id") == model), {}) if model else {}
    if "enabled" in body:
        if bool(body["enabled"]) != row.enabled:
            changed.append("enabled")
        row.enabled = bool(body["enabled"])
    if "pricing" in body:
        row.pricing = _validate_pricing(body.get("pricing"))
        changed.append("pricing")
    if role == "challenger" and isinstance(body.get("review_policy"), dict):
        row.review_policy = {k: bool(v) for k, v in body["review_policy"].items() if k in REVIEWS}
        changed.append("review_policy")
    if any(c in changed for c in ("provider", "api_key", "model")):
        row.status = "UNTESTED" if (row.provider and row.model and _key(row)) else "NOT_CONFIGURED"
        row.last_test_ok, row.last_test_steps, row.last_error, row.last_error_kind = None, [], "", ""
        row.source = "configured"
    if row.enabled and not (row.provider and row.model and _key(row)):
        raise ValidationFailed(f"{ROLES[role]} needs a provider, a model and an API key before it can be turned on")
    row.updated_by_id = getattr(user, "id", None)
    db.flush()
    activity.log(db, "ai_role.saved", actor=user, entity="ai_role", entity_id=row.id,
                 details={"role": role, "provider": row.provider, "model": row.model, "enabled": row.enabled, "changed": changed})
    invalidate()
    return row


def revoke(db: Session, role: str, user) -> AIRoleConfig:
    row = _row(db, role)
    if row is None:
        raise NotFound(f"{ROLES[role]} is not configured")
    row.secrets_ciphertext, row.key_fingerprint, row.credential_ref = "", "", ""
    row.enabled, row.status, row.last_test_ok = False, "NOT_CONFIGURED", None
    db.flush()
    activity.log(db, "ai_role.key_revoked", actor=user, entity="ai_role", entity_id=row.id, details={"role": role, "provider": row.provider})
    invalidate()
    return row


def _err(exc: AdapterError) -> dict:
    return {"kind": exc.details.get("error_kind") or exc.kind, "message": exc.message}


def _candidate(db: Session, role: str, body: dict) -> tuple[AIRoleConfig, str, str, str, bool]:
    """(row, provider, model, key, uses_saved_config) for a test or discovery: unsaved values from the form win, else the saved ones."""
    row = _row(db, role, create=True)
    provider = (body.get("provider") or row.provider or "").strip()
    if provider not in P.SPECS:
        raise ValidationFailed("Choose a provider first")
    typed = (body.get("api_key") or "").strip()
    key = typed or (_key(row) if provider == row.provider else "")
    if not key:
        raise ValidationFailed(f"Enter the {P.SPECS[provider].label} API key first")
    model = (body.get("model") if "model" in body else row.model) or ""
    saved = not typed and provider == row.provider and model == row.model
    return row, provider, model.strip(), key, saved


def discover(db: Session, role: str, body: dict, *, http=None) -> dict:
    row, provider, _model, key, _ = _candidate(db, role, body)
    fresh = row.provider == provider and row.models_fetched_at and clock.real_now() - row.models_fetched_at < MODELS_TTL and not body.get("refresh") \
        and not body.get("api_key")
    if fresh:
        models = row.models_cache or []
        return _model_lists(models, row.models_fetched_at.isoformat(), cached=True)
    try:
        found = [m.to_dict() for m in P.list_models(provider, key, http=http)]
    except AdapterError as exc:
        e = _err(exc)
        if provider == row.provider and not body.get("api_key"):
            row.models_error = e["message"][:300]
        hint = {"auth_failure": "The provider rejected the API key.", "outage": "The provider is unavailable right now.",
                "network": "The provider could not be reached.", "timeout": "The provider did not answer in time."}.get(e["kind"],
                "Model discovery is unavailable; enter a model id manually (it is checked with a real request).")
        return {"models": [], "hidden": [], "error": {**e, "hint": hint}, "fetched_at": None, "manual_entry": True}
    found.sort(key=lambda m: m["id"])
    now = clock.real_now()
    if provider == row.provider and not body.get("api_key"):
        row.models_cache, row.models_fetched_at, row.models_error = found, now, ""
        if row.model:
            row.model_meta = next((m for m in found if m["id"] == row.model), row.model_meta or {})
    return _model_lists(found, now.isoformat(), cached=False)


def _model_lists(models: list[dict], fetched_at, *, cached: bool) -> dict:
    return {"models": [m for m in models if not m.get("hidden_reason")], "hidden": [m for m in models if m.get("hidden_reason")],
            "fetched_at": fetched_at, "cached": cached, "error": None, "manual_entry": True}


def test(db: Session, role: str, body: dict, user, *, http=None) -> dict:
    """Real requests only: (1) an authenticated read proves the key, (2) a tiny JSON completion proves the model answers.
    The saved configuration's status is updated only when the saved configuration was tested."""
    row, provider, model, key, saved = _candidate(db, role, body)
    steps, ok, err = [], True, None
    try:
        P.check_key(provider, key, http=http)
        steps.append({"name": "API key", "ok": True, "detail": f"{P.SPECS[provider].label} accepted the key"})
    except AdapterError as exc:
        ok, err = False, _err(exc)
        steps.append({"name": "API key", "ok": False, "detail": exc.message, "error_kind": err["kind"]})
    if ok and not model:
        ok, err = False, {"kind": "unsupported_model", "message": "Choose or enter a model"}
        steps.append({"name": "Model", "ok": False, "detail": err["message"], "error_kind": err["kind"]})
    elif ok:
        try:
            meta = next((m for m in (row.models_cache or []) if m.get("id") == model), {}) if provider == row.provider else {}
            prov = P.build(provider, key, model, http=http, model_meta=meta)
            raw = prov.complete("CONNECTION_TEST", TEST_PROMPT, {"check": "ok"}, max_tokens=400)
            answered = bool(raw.text.strip()) or raw.finish_reason == "length"
            json_ok = False
            try:
                json_ok = json.loads(raw.text.strip().strip("`").removeprefix("json").strip()).get("ok") is True
            except (ValueError, AttributeError):
                pass
            detail = (f"{model} answered ({raw.tokens_in} prompt + {raw.tokens_out} completion tokens)"
                      + ("" if json_ok else "; the answer was not the requested JSON, so structured output must be validated per task"))
            steps.append({"name": "Model", "ok": answered, "detail": detail if answered else f"{model} returned an empty answer",
                          "json": json_ok, "usage": {"input": raw.tokens_in, "output": raw.tokens_out}})
            ok = answered
            if not answered:
                err = {"kind": "malformed_json", "message": f"{model} returned an empty answer"}
        except AdapterError as exc:
            ok, err = False, _err(exc)
            steps.append({"name": "Model", "ok": False, "detail": exc.message, "error_kind": err["kind"]})
    if saved:
        row.last_test_at, row.last_test_ok, row.last_test_steps = clock.real_now(), ok, steps
        row.status = "CONNECTED" if ok else "FAILED"
        row.last_error, row.last_error_kind = (err or {}).get("message", "")[:500], (err or {}).get("kind", "")
        activity.log(db, "ai_role.tested", actor=user, entity="ai_role", entity_id=row.id,
                     details={"role": role, "provider": provider, "model": model, "ok": ok, "error_kind": (err or {}).get("kind")})
        invalidate()
    return {"ok": ok, "steps": steps, "error": err, "recorded": saved, "provider": provider, "model": model}
