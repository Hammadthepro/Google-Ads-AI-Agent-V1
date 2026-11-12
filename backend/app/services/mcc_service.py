"""Manager-account (MCC) based multi-client management. READ-ONLY toward Google Ads.

Flow: OAuth connection (integrations) -> candidate managers -> the user selects one (stored on the workspace) -> discovery walks the
`customer_client` hierarchy and upserts every account -> users pick the account they work in (stored per user and workspace).

Customer IDs are stored exactly as the API needs them: 10 digits, no dashes. Discovery never deletes an account: accounts that disappear
are marked NOT_SEEN and kept, and a user's selection survives refreshes.
"""
from __future__ import annotations

import re

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.adapters import registry
from app.adapters.base import AdapterError
from app.core import clock
from app.core.errors import NotFound, ValidationFailed
from app.models import GoogleAdsAccount, User, UserAccountSelection, Workspace
from app.services import activity, auth_service, integrations_service

DEFAULT_SLUG = "default"
USABLE = {"ENABLED"}
STATUSES = ("ENABLED", "CANCELED", "SUSPENDED", "CLOSED", "UNKNOWN")


def normalize_customer_id(value: str) -> str:
    """'123-456-7890' / ' 1234567890 ' -> '1234567890' (the format the Google Ads API requires)."""
    digits = re.sub(r"[\s-]", "", value or "")
    if not (digits.isdigit() and len(digits) == 10):
        raise ValidationFailed("A Google Ads customer ID is exactly 10 digits (dashes are allowed when typing).")
    return digits


def format_customer_id(value: str) -> str:
    return f"{value[:3]}-{value[3:6]}-{value[6:]}" if len(value) == 10 else value


def default_workspace(db: Session) -> Workspace:
    ws = db.scalar(select(Workspace).where(Workspace.slug == DEFAULT_SLUG))
    if ws is None:
        ws = Workspace(name="Default workspace", slug=DEFAULT_SLUG)
        db.add(ws)
        db.flush()
    return ws


def _status(value: str | None) -> str:
    v = (value or "UNKNOWN").upper()
    return v if v in STATUSES else "UNKNOWN"


def serialize_account(a: GoogleAdsAccount) -> dict:
    usable = a.status in USABLE and not a.is_manager and a.discovery_state == "ACTIVE" and not a.discovery_error
    reason = ""
    if a.is_manager:
        reason = "Manager account (holds no campaigns)"
    elif a.status != "ENABLED":
        reason = f"Account is {a.status.lower()}"
    elif a.discovery_error:
        reason = f"Could not be read: {a.discovery_error}"
    elif a.discovery_state != "ACTIVE":
        reason = "Not seen in the last refresh"
    return {"id": a.id, "customer_id": a.customer_id, "customer_id_display": format_customer_id(a.customer_id), "name": a.descriptive_name,
            "status": a.status, "is_manager": a.is_manager, "currency_code": a.currency_code, "time_zone": a.time_zone, "level": a.level,
            "parent_customer_id": a.parent_customer_id, "manager_customer_id": a.login_customer_id, "is_test_account": a.is_test_account,
            "discovery_state": a.discovery_state, "discovery_error": a.discovery_error, "selectable": usable, "unavailable_reason": reason,
            "client_id": a.client_id, "last_discovered_at": a.last_discovered_at.isoformat() if a.last_discovered_at else None}


# ------------------------------------------------------------------ the one answer to "which manager is selected?"
def effective_manager(db: Session) -> dict:
    """The manager every part of the app uses. The workspace's explicit choice wins; otherwise the manager ID saved on the Google Ads
    integration (settings or GOOGLE_ADS_LOGIN_CUSTOMER_ID) counts as the selection. `source` says which one answered."""
    ws = default_workspace(db)
    if ws.google_ads_manager_customer_id:
        return {"customer_id": ws.google_ads_manager_customer_id, "name": ws.google_ads_manager_name, "source": "workspace"}
    saved = re.sub(r"[\s-]", "", integrations_service.resolve("google_ads").get("login_customer_id") or "")
    if saved.isdigit() and len(saved) == 10:
        name = db.scalar(select(GoogleAdsAccount.descriptive_name).where(GoogleAdsAccount.customer_id == saved)) or ""
        return {"customer_id": saved, "name": name, "source": "integration_setting"}
    return {"customer_id": "", "name": "", "source": ""}


# ------------------------------------------------------------------ step 1: choose the manager
def manager_candidates(db: Session) -> dict:
    """Accounts this Google user can reach directly; managers are the possible MCCs. Needs the OAuth connection."""
    ads = registry.get("google_ads")  # IntegrationNotConfigured -> "configure Google Ads on the Integrations page"
    ws = default_workspace(db)
    chosen = effective_manager(db)["customer_id"]
    items = ads.list_manager_candidates()
    for i in items:
        i["customer_id_display"] = format_customer_id(i["customer_id"])
        i["selected"] = i["customer_id"] == chosen
        i["selectable"] = bool(i["is_manager"]) and i["status"] == "ENABLED" and not i["error"]
        i["note"] = ("Closed, canceled or suspended" if i["is_manager"] and i["status"] != "ENABLED" else
                     "Regular account (not a manager)" if i["is_manager"] is False else
                     f"Could not be read ({i['error']})" if i["error"] else "")
    return {"workspace_id": ws.id, "selected_manager": chosen or None,
            "candidates": items, "managers": [i for i in items if i["selectable"]]}


def select_manager(db: Session, user: User, customer_id: str) -> dict:
    auth_service.require(user, "integrations.manage")
    cid = normalize_customer_id(customer_id)
    info = next((c for c in manager_candidates(db)["candidates"] if c["customer_id"] == cid), None)
    if info is None:
        raise NotFound("That account is not accessible to the connected Google user")
    if not info["selectable"]:
        raise ValidationFailed(f"{cid} cannot be used as the manager account: {info['note'] or 'it is not an enabled manager account'}.")
    ws = default_workspace(db)
    changed = ws.google_ads_manager_customer_id != cid
    ws.google_ads_manager_customer_id, ws.google_ads_manager_name = cid, info["descriptive_name"]
    if changed:
        ws.discovery_status, ws.discovery_message = "NEVER_RUN", ""
    activity.log(db, "google_ads.manager_selected", actor=user, entity="workspace", entity_id=ws.id,
                 details={"manager_customer_id": cid, "name": info["descriptive_name"], "changed": changed})
    db.flush()
    integrations_service.invalidate()
    return workspace_summary(db)


def workspace_summary(db: Session) -> dict:
    ws = default_workspace(db)
    mgr = effective_manager(db)
    in_ws = GoogleAdsAccount.workspace_id == ws.id
    total = db.scalar(select(func.count(GoogleAdsAccount.id)).where(in_ws, GoogleAdsAccount.is_manager.is_(False))) or 0
    managers = [serialize_account(a) for a in db.scalars(select(GoogleAdsAccount).where(in_ws, GoogleAdsAccount.is_manager.is_(True))
                                                         .order_by(GoogleAdsAccount.level, GoogleAdsAccount.customer_id))]
    return {"workspace": {"id": ws.id, "name": ws.name, "slug": ws.slug}, "manager_customer_id": mgr["customer_id"] or None,
            "manager_name": mgr["name"], "manager_source": mgr["source"] or None,
            "accounts_discovered_at": ws.accounts_discovered_at.isoformat() if ws.accounts_discovered_at else None,
            "discovery_status": ws.discovery_status, "discovery_message": ws.discovery_message, "client_accounts": total,
            "manager_accounts": managers}


def _no_clients_reason(manager: str, sub_managers: list[dict], errors: list[dict]) -> str:
    """Why a completed read produced no client accounts, from what Google Ads actually returned."""
    if errors:
        return (f"No client accounts could be read below manager {manager}: {len(errors)} branch(es) failed ("
                + ", ".join(sorted({e['code'] for e in errors})) + ").")
    if not sub_managers:
        return f"Manager {manager} has no accounts linked to it in Google Ads."
    names = ", ".join(f"{m.get('descriptive_name') or 'unnamed'} ({m['customer_id']})" for m in sub_managers[:5])
    return (f"Manager {manager} has no client accounts. The only account(s) linked to it are manager account(s): {names}, with no client "
            "accounts below. Link a client account to one of these managers in Google Ads, or choose a different manager.")


# ------------------------------------------------------------------ step 2: discover the hierarchy
def discover(db: Session, user: User | None = None) -> dict:
    """Walk the hierarchy below the selected manager and persist it. Safe to repeat: upserts only; selections are untouched."""
    if user is not None:
        auth_service.require(user, "clients.edit")
    ws = default_workspace(db)
    manager = effective_manager(db)["customer_id"]
    if not manager:
        raise ValidationFailed("Select a manager (MCC) account first: Integrations > Google Ads > Choose manager account.")
    manager = normalize_customer_id(manager)
    ads = registry.get("google_ads")
    try:
        res = ads.list_customer_clients(manager)
    except AdapterError as exc:
        ws.discovery_status, ws.discovery_message = "FAILED", exc.message[:500]
        db.commit()  # the failure is recorded even though the request errors; previously discovered accounts are untouched
        raise
    root = next((a for a in res["accounts"] if normalize_customer_id(a["customer_id"]) == manager), None)
    root_unreadable = any(e["customer_id"] == manager for e in res.get("errors", []))  # reported below as a partial read with its error code
    problem = ("" if root_unreadable else
               f"Google Ads returned no record for manager {manager}. Check that the connected Google user can still access it." if root is None
               else f"{manager} is a regular Google Ads account, not a manager (MCC), so it has no client accounts. Choose a manager account."
               if not root.get("is_manager") else "")
    if problem:
        ws.discovery_status, ws.discovery_message = "FAILED", problem
        db.commit()
        raise ValidationFailed(problem)
    if ws.google_ads_manager_customer_id != manager:  # the saved integration setting was used: make it the workspace's stored selection
        ws.google_ads_manager_customer_id = manager
        activity.log(db, "google_ads.manager_selected", actor=user, entity="workspace", entity_id=ws.id,
                     details={"manager_customer_id": manager, "source": "integration_setting"})
        integrations_service.invalidate()
    ws.google_ads_manager_name = (root or {}).get("descriptive_name") or ws.google_ads_manager_name
    now = clock.now()
    seen: set[str] = set()
    for a in res["accounts"]:
        cid = normalize_customer_id(a["customer_id"])
        seen.add(cid)
        row = db.scalar(select(GoogleAdsAccount).where(GoogleAdsAccount.customer_id == cid))
        if row is None:
            row = GoogleAdsAccount(customer_id=cid)
            db.add(row)
        row.workspace_id, row.login_customer_id = ws.id, manager
        row.descriptive_name = a.get("descriptive_name") or row.descriptive_name or ""
        row.status = _status(a.get("status"))
        row.is_manager = bool(a.get("is_manager"))
        row.level = int(a.get("level") or 0)
        row.parent_customer_id = a.get("parent_customer_id") or ""
        row.currency_code = a.get("currency_code") or row.currency_code
        row.time_zone = a.get("time_zone") or row.time_zone
        row.is_test_account, row.is_hidden = bool(a.get("test_account")), bool(a.get("hidden"))
        row.discovery_state, row.discovery_error, row.last_discovered_at = "ACTIVE", a.get("error") or "", now
        row.integration_mode = adapter_mode_label(ads)
        row.permission_status = "OK" if row.status == "ENABLED" and not row.discovery_error else (row.discovery_error or row.status)
    errors = res.get("errors", [])
    for e in errors:  # a branch we could not read: keep what we know, record why
        row = db.scalar(select(GoogleAdsAccount).where(GoogleAdsAccount.customer_id == e["customer_id"]))
        if row is not None:
            row.discovery_error, row.permission_status = e["code"][:64], e["code"][:32]
    if not errors:  # only a complete walk may say an account vanished; it is marked, never deleted
        for row in db.scalars(select(GoogleAdsAccount).where(GoogleAdsAccount.workspace_id == ws.id, GoogleAdsAccount.login_customer_id == manager,
                                                             GoogleAdsAccount.discovery_state == "ACTIVE")):
            if row.customer_id not in seen:
                row.discovery_state = "NOT_SEEN"
    ws.accounts_discovered_at = now
    ws.discovery_status = "PARTIAL" if errors else "OK"
    counts = {s: sum(1 for a in res["accounts"] if _status(a.get("status")) == s) for s in STATUSES}
    clients = [a for a in res["accounts"] if not a.get("is_manager")]
    sub_managers = [a for a in res["accounts"] if a.get("is_manager") and normalize_customer_id(a["customer_id"]) != manager]
    reason = "" if clients else _no_clients_reason(manager, sub_managers, errors)
    if reason and not errors:
        ws.discovery_status = "NO_CLIENTS"
    ws.discovery_message = (reason or f"{len(clients)} client account(s) and {len(sub_managers)} sub-manager account(s) found"
                            + (f"; {len(errors)} branch(es) could not be read (" + ", ".join(sorted({e['code'] for e in errors})) + ")" if errors else ""))[:500]
    activity.log(db, "google_ads.accounts_discovered", actor=user, entity="workspace", entity_id=ws.id,
                 details={"manager": manager, "found": len(res["accounts"]), "client_accounts": len(clients), "sub_managers": len(sub_managers),
                          "by_status": counts, "branch_errors": errors[:20]})
    db.flush()
    return {**workspace_summary(db), "found": len(res["accounts"]), "client_accounts_found": len(clients), "sub_managers_found": len(sub_managers),
            "no_clients_reason": reason, "by_status": counts, "errors": errors}


def adapter_mode_label(ads) -> str:
    return "REAL" if getattr(ads, "is_real", False) else "MOCK"


# ------------------------------------------------------------------ step 3: list / filter
def accessible_accounts(db: Session, user: User, workspace: Workspace | None = None):
    """The single place that decides which accounts a user may see. Today: everyone in the workspace sees all of its accounts.
    Later (user-to-account assignments) the restriction is added HERE and every caller inherits it."""
    ws = workspace or default_workspace(db)
    return select(GoogleAdsAccount).where(GoogleAdsAccount.workspace_id == ws.id)


def list_accounts(db: Session, user: User, *, q: str = "", status: str = "", include_managers: bool = False,
                  only_selectable: bool = False, limit: int = 200, offset: int = 0) -> dict:
    stmt = accessible_accounts(db, user)
    if not include_managers:
        stmt = stmt.where(GoogleAdsAccount.is_manager.is_(False))
    if status:
        stmt = stmt.where(GoogleAdsAccount.status == status.upper())
    if q.strip():
        like = f"%{q.strip()}%"
        digits = re.sub(r"\D", "", q)
        conds = [GoogleAdsAccount.descriptive_name.ilike(like)]
        if digits:
            conds.append(GoogleAdsAccount.customer_id.like(f"%{digits}%"))
        stmt = stmt.where(or_(*conds))
    rows = [serialize_account(a) for a in db.scalars(stmt.order_by(GoogleAdsAccount.descriptive_name, GoogleAdsAccount.customer_id))]
    if only_selectable:
        rows = [r for r in rows if r["selectable"]]
    return {"total": len(rows), "items": rows[offset: offset + max(1, min(limit, 500))], **workspace_summary(db)}


# ------------------------------------------------------------------ step 4: selection (per user, per workspace)
def get_selection(db: Session, user: User) -> dict:
    ws = default_workspace(db)
    sel = db.scalar(select(UserAccountSelection).where(UserAccountSelection.user_id == user.id, UserAccountSelection.workspace_id == ws.id))
    if sel is None:
        return {"selected": None}
    acct = db.scalar(accessible_accounts(db, user, ws).where(GoogleAdsAccount.customer_id == sel.customer_id))
    return {"selected": serialize_account(acct) if acct else {"customer_id": sel.customer_id, "name": "", "selectable": False,
                                                              "unavailable_reason": "No longer visible in this workspace"},
            "selected_at": sel.updated_at.isoformat()}


def select_account(db: Session, user: User, customer_id: str) -> dict:
    cid = normalize_customer_id(customer_id)
    acct = db.scalar(accessible_accounts(db, user).where(GoogleAdsAccount.customer_id == cid))
    if acct is None:
        raise NotFound("That account is not in this workspace. Refresh the accounts list.")
    ser = serialize_account(acct)
    if not ser["selectable"]:
        raise ValidationFailed(f"This account cannot be selected: {ser['unavailable_reason']}.")
    ws = default_workspace(db)
    sel = db.scalar(select(UserAccountSelection).where(UserAccountSelection.user_id == user.id, UserAccountSelection.workspace_id == ws.id))
    previous = sel.customer_id if sel else None
    if sel is None:
        sel = UserAccountSelection(user_id=user.id, workspace_id=ws.id, customer_id=cid)
        db.add(sel)
    sel.customer_id = cid
    activity.log(db, "google_ads.account_selected", actor=user, entity="google_ads_account", entity_id=acct.id,
                 client_id=acct.client_id, details={"customer_id": cid, "previous": previous})
    db.flush()
    return get_selection(db, user)
