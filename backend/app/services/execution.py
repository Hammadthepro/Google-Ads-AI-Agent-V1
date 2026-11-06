"""Campaign execution engine (step 9).

Lifecycle: approved payload -> hash/version check -> WriteGuard -> re-validate -> manual-change check ->
Operation row committed (idempotency key) -> adapter.mutate (creates are PAUSED) -> read-back verification ->
ENABLE approval created (enabling is a separate approved action).
A timeout/unknown result is NEVER blindly retried: the account state is read and reconciled first.
"""
from __future__ import annotations

import hashlib
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import registry
from app.adapters.base import AdapterError, AdapterTimeout
from app.core import clock
from app.core.errors import Conflict, NotFound, SafetyBlocked
from app.core.util import normalize_term, payload_hash, redact
from app.models import Ad, AdGroup, Approval, Business, Campaign, Client, GoogleAdsAccount, Operation, User
from app.services import accounts_service, activity, approvals, safety, validation

STATUS_RESOURCES = ("campaign", "ad_group", "ad_group_ad", "ad_group_criterion")


def _op_type(payload: dict) -> str:
    return payload.get("kind", "MUTATE")


def _identity(op: dict, temp_map: dict) -> tuple:
    f = op.get("fields") or {}

    def r(v):
        return temp_map.get(v[5:], v) if isinstance(v, str) and v.startswith("temp:") else v
    rt = op["resource"]
    if rt in ("campaign", "campaign_budget", "shared_set"):
        return (rt, f.get("name"))
    if rt == "ad_group":
        return (rt, r(f.get("campaign")), f.get("name"))
    if rt in ("ad_group_criterion", "campaign_criterion", "shared_criterion"):
        kw = f.get("keyword") or {}
        parent = r(f.get("ad_group") or f.get("campaign") or f.get("shared_set"))
        return (rt, parent, f.get("type"), normalize_term(kw.get("text", "")), kw.get("match_type"), bool(f.get("negative")),
                str(f.get("proximity") or f.get("location") or ""))
    if rt == "ad_group_ad":
        rsa = ((f.get("ad") or {}).get("responsive_search_ad") or {})
        return (rt, r(f.get("ad_group")), tuple(rsa.get("headlines", [])))
    if rt == "campaign_asset":
        return (rt, r(f.get("campaign")), r(f.get("asset")), f.get("field_type"))
    if rt == "campaign_shared_set":
        return (rt, r(f.get("campaign")), r(f.get("shared_set")))
    if rt == "asset":
        return (rt, str(f.get("call_asset") or f.get("sitelink_asset") or f.get("callout_asset")))
    return (rt, str(sorted(f.items())))


def _existing_identity(res: dict) -> tuple:
    rt = res["resource_type"]
    if rt in ("campaign", "campaign_budget", "shared_set"):
        return (rt, res.get("name"))
    if rt == "ad_group":
        return (rt, res.get("campaign"), res.get("name"))
    if rt in ("ad_group_criterion", "campaign_criterion", "shared_criterion"):
        kw = res.get("keyword") or {}
        parent = res.get("ad_group") or res.get("campaign") or res.get("shared_set")
        return (rt, parent, res.get("type"), normalize_term(kw.get("text", "")), kw.get("match_type"), bool(res.get("negative")),
                str(res.get("proximity") or res.get("location") or ""))
    if rt == "ad_group_ad":
        rsa = ((res.get("ad") or {}).get("responsive_search_ad") or {})
        return (rt, res.get("ad_group"), tuple(rsa.get("headlines", [])))
    if rt == "campaign_asset":
        return (rt, res.get("campaign"), res.get("asset"), res.get("field_type"))
    if rt == "campaign_shared_set":
        return (rt, res.get("campaign"), res.get("shared_set"))
    if rt == "asset":
        return (rt, str(res.get("call_asset") or res.get("sitelink_asset") or res.get("callout_asset")))
    return (rt, "")


def reconcile(db: Session, op_row: Operation) -> Operation:
    """Read the account and decide whether an UNKNOWN operation was applied. Never re-sends blindly."""
    ads = registry.get("google_ads")
    cid = op_row.request_payload["customer_id"]
    ops = op_row.request_payload["operations"]
    creates = [o for o in ops if o["op"] == "create"]
    cache: dict[str, list[dict]] = {}
    temp_map: dict[str, str] = {}
    found = []
    for o in creates:
        rt = o["resource"]
        if rt not in cache:
            cache[rt] = ads.list_resources(cid, rt)
        ident = _identity(o, temp_map)
        match = next((r for r in cache[rt] if _existing_identity(r) == ident), None)
        if match:
            found.append({"op": "create", "resource": rt, "resource_name": match["resource_name"], "temp_id": o.get("temp_id")})
            if o.get("temp_id"):
                temp_map[str(o["temp_id"])] = match["resource_name"]
    op_row.reconciled_at = clock.now()
    if creates and len(found) == len(creates):
        op_row.state = "SUCCEEDED"
        op_row.external_resource_names = [f["resource_name"] for f in found]
        op_row.external_response = {"reconciled": True, "results": found}
    elif not found:
        op_row.state = "NOT_APPLIED"
        op_row.error = "Reconciliation: nothing from this operation exists in the account; safe to re-submit."
    else:
        op_row.state = "RECONCILE_CONFLICT"
        op_row.error = f"Reconciliation found {len(found)}/{len(creates)} resources — human review required."
    activity.log(db, "operation.reconciled", entity="operation", entity_id=op_row.id, client_id=op_row.client_id,
                 details={"state": op_row.state, "found": len(found), "expected": len(creates)},
                 integration_mode=op_row.integration_mode)
    return op_row


def verify_read_back(op_row: Operation) -> tuple[bool, list]:
    ads = registry.get("google_ads")
    cid = op_row.request_payload["customer_id"]
    ops = op_row.request_payload["operations"]
    results = (op_row.external_response or {}).get("results", [])
    diffs = []
    for o, res in zip(ops, results):
        actual = ads.get_resource(cid, res["resource_name"])
        if not actual:
            diffs.append({"resource": res["resource_name"], "problem": "not found on read-back"})
            continue
        f = o.get("fields") or {}
        if o["op"] == "create":
            if o["resource"] in STATUS_RESOURCES and not f.get("negative") and actual.get("status") != "PAUSED":
                diffs.append({"resource": res["resource_name"], "problem": f"status {actual.get('status')} (expected PAUSED)"})
            for key in ("name", "amount_micros", "keyword", "advertising_channel_type"):
                if key in f and actual.get(key) != f[key] and not (isinstance(f[key], str) and f[key].startswith("temp:")):
                    diffs.append({"resource": res["resource_name"], "problem": f"{key} mismatch", "expected": f[key], "actual": actual.get(key)})
            if o["resource"] == "ad_group_ad":
                exp = (f.get("ad") or {}).get("responsive_search_ad", {}).get("headlines")
                got = (actual.get("ad") or {}).get("responsive_search_ad", {}).get("headlines")
                if exp != got:
                    diffs.append({"resource": res["resource_name"], "problem": "headlines mismatch"})
        else:
            if "status" in f and actual.get("status") != f["status"]:
                diffs.append({"resource": res["resource_name"], "problem": f"status {actual.get('status')} (expected {f['status']})"})
            for key, val in f.items():
                if key != "status" and actual.get(key) != val:
                    diffs.append({"resource": res["resource_name"], "problem": f"{key} mismatch", "expected": val, "actual": actual.get(key)})
    return not diffs, diffs


def manual_change_conflicts(db: Session, account: GoogleAdsAccount, payload: dict, since) -> list[dict]:
    """Changes made in Google Ads by someone else (not our API user) since the approval was created."""
    ads = registry.get("google_ads")
    touched = {o.get("resource_name") for o in payload.get("operations", []) if o.get("op") == "update"}
    parents = {(o.get("fields") or {}).get("campaign") or (o.get("fields") or {}).get("ad_group")
               for o in payload.get("operations", [])} - {None}
    touched |= {p for p in parents if isinstance(p, str) and not p.startswith("temp:")}
    if not touched:
        return []
    try:
        events = ads.get_change_events(account.customer_id, since.isoformat())
    except AdapterError:
        return []
    return [e for e in events if e.get("client_type") != "GOOGLE_ADS_API" and
            any(e["change_resource_name"] == t or e["change_resource_name"].startswith(t + "~") or t.startswith(e["change_resource_name"]) for t in touched)]


def _apply_internal(db: Session, approval: Approval, payload: dict) -> None:
    internal = payload.get("internal") or {}
    cid = (payload.get("target") or {}).get("campaign_id")
    if cid and "weekly_conversion_target" in internal and payload.get("kind") == "CHANGE_TARGET":
        c = db.get(Campaign, cid)
        if c:
            c.weekly_conversion_target = float(internal["weekly_conversion_target"])
            c.last_changed_at = clock.now()
    if cid and "monthly_budget" in internal and payload.get("kind") == "CHANGE_BUDGET":
        c = db.get(Campaign, cid)
        if c:
            c.monthly_budget_micros = int(internal["monthly_budget"] * 1e6)


def _enable_payload(op_row: Operation, payload: dict) -> dict | None:
    results = (op_row.external_response or {}).get("results", [])
    ops = []
    for o, res in zip(payload["operations"], results):
        if o["op"] == "create" and o["resource"] in STATUS_RESOURCES and not (o.get("fields") or {}).get("negative"):
            ops.append({"resource": o["resource"], "op": "update", "resource_name": res["resource_name"], "fields": {"status": "ENABLED"}})
    replaces = (payload.get("internal") or {}).get("replaces_ad")
    if replaces:
        ops.append({"resource": "ad_group_ad", "op": "update", "resource_name": replaces, "fields": {"status": "PAUSED"}})
    if not ops:
        return None
    return {"kind": "ENABLE", "customer_id": payload["customer_id"], "operations": ops, "parent_operation_id": op_row.id,
            "summary": {"enable": len([o for o in ops if o["fields"]["status"] == "ENABLED"]),
                        "pause_replaced_ad": bool(replaces), "verified_paused_resources": True},
            "internal": {"business_id": (payload.get("internal") or {}).get("business_id")}}


def execute(db: Session, actor: User, approval_id: str) -> Operation:
    a = db.get(Approval, approval_id)
    if not a:
        raise NotFound("Approval not found")
    existing = db.scalar(select(Operation).where(Operation.approval_id == a.id).order_by(Operation.created_at.desc()))
    if existing and existing.state in ("SUCCEEDED", "PENDING", "SENT"):
        return existing  # idempotent: never execute an approval twice
    if existing and existing.state == "UNKNOWN":
        reconcile(db, existing)
        if existing.state != "NOT_APPLIED":
            db.commit()
            return existing
    client = db.get(Client, a.client_id)
    account = db.get(GoogleAdsAccount, a.google_ads_account_id) if a.google_ads_account_id else None
    payload = a.payload
    adapter = registry.get("google_ads")
    try:
        safety.check(db, policy="APPROVAL", client=client, account=account, approval=a, actor=actor,
                     business_id=a.business_id, payload=payload)
    except SafetyBlocked as exc:
        activity.log(db, "execution.blocked", actor=actor, entity="approval", entity_id=a.id, client_id=a.client_id,
                     details=exc.details)
        db.commit()
        raise
    # enabling requires a verified parent operation
    if a.approval_type == "ENABLE":
        parent = db.get(Operation, a.parent_operation_id) if a.parent_operation_id else None
        if not parent or parent.state != "SUCCEEDED" or not parent.verification_passed:
            raise SafetyBlocked("Cannot enable: the paused resources were not created and verified",
                                details={"reasons": ["parent operation not verified"]})
    business = db.get(Business, a.business_id) if a.business_id else None
    if business and payload.get("operations"):
        v = validation.validate(db, payload, business, approval_id=a.id, run_adapter=True,
                                check_landing_pages=a.approval_type != "PAUSE")
        if not v.passed:
            a.status = "EXECUTION_BLOCKED"
            activity.log(db, "execution.revalidation_failed", actor=actor, entity="approval", entity_id=a.id,
                         client_id=a.client_id, details={"errors": v.errors[:10]})
            db.commit()
            raise SafetyBlocked("Re-validation before execution failed", details={"reasons": [e["message"] for e in v.errors[:10]]})
    if account and payload.get("operations"):
        conflicts = manual_change_conflicts(db, account, payload, a.created_at - timedelta(minutes=1))
        if conflicts:
            a.status = "EXECUTION_BLOCKED"
            activity.log(db, "execution.manual_change_conflict", actor=actor, entity="approval", entity_id=a.id,
                         client_id=a.client_id, details={"changes": conflicts[:5]})
            db.commit()
            raise SafetyBlocked("Someone changed these resources in Google Ads after this approval was created",
                                details={"reasons": [f"{c['user_email']} changed {c['change_resource_name']}" for c in conflicts[:5]]})
    key = hashlib.sha256(f"{a.id}|{a.approved_payload_hash}|{(existing.attempts if existing else 0) + 1}".encode()).hexdigest()
    op_row = Operation(client_id=a.client_id, google_ads_account_id=a.google_ads_account_id, approval_id=a.id, policy="APPROVAL",
                       idempotency_key=key, tracking_label="gaa-" + key[:10],
                       adapter_name=adapter.name if payload.get("operations") else "internal",
                       integration_mode="REAL" if adapter.is_real else "MOCK", operation_type=_op_type(payload),
                       request_payload=payload, payload_hash=a.approved_payload_hash, state="PENDING",
                       attempts=(existing.attempts if existing else 0) + 1, requested_by_id=a.requested_by_id,
                       approved_by_id=a.decided_by_id)
    db.add(op_row)
    a.status = "EXECUTING"
    db.commit()  # the operation record exists BEFORE any external call
    if payload.get("operations"):
        try:
            op_row.state = "SENT"
            db.commit()
            resp = adapter.mutate(payload["customer_id"], payload["operations"], request_id=op_row.idempotency_key)
            op_row.external_response = redact(resp)
            op_row.external_resource_names = [r["resource_name"] for r in resp.get("results", [])]
            op_row.state = "SUCCEEDED"
        except AdapterTimeout as exc:
            op_row.state, op_row.error = "UNKNOWN", f"timeout: {exc.message}"
            db.commit()
            reconcile(db, op_row)
        except AdapterError as exc:
            op_row.state, op_row.error = "FAILED", f"{exc.kind}: {exc.message}"
            op_row.external_response = redact(exc.to_dict())
    else:
        op_row.state = "SUCCEEDED"
        op_row.external_response = {"internal": True}
    if op_row.state == "SUCCEEDED":
        if payload.get("operations"):
            ok, diffs = verify_read_back(op_row)
            op_row.verification_passed, op_row.verification_diff = ok, diffs
        else:
            op_row.verification_passed = True
        _apply_internal(db, a, payload)
        a.status = "EXECUTED"
        if account:
            accounts_service.sync_account(db, account)
            _post_execution_updates(db, a, payload, op_row)
        if op_row.verification_passed and a.approval_type in ("CREATE", "UPDATE") and payload.get("kind") not in ("PAUSE_AD",):
            enable = _enable_payload(op_row, payload)
            if enable:
                approvals.create(db, client_id=a.client_id, approval_type="ENABLE", title=f"Enable: {a.title}",
                                 payload=enable, requested_by=None, business_id=a.business_id, account_id=a.google_ads_account_id,
                                 task_id=a.task_id, current_state={"status": "PAUSED (verified by read-back)"},
                                 proposed_state={"status": "ENABLED"}, reason="Resources were created PAUSED and verified. "
                                 "Turning them on requires this separate approval.",
                                 expected_effect="Ads start serving and spending budget.",
                                 risks=["Spend starts immediately after enabling"], context={"parent_approval_id": a.id},
                                 parent_operation_id=op_row.id)
    else:
        a.status = "EXECUTION_FAILED" if op_row.state in ("FAILED", "NOT_APPLIED", "RECONCILE_CONFLICT") else a.status
    if a.approval_type == "ENABLE" and op_row.state == "SUCCEEDED":
        parent = db.get(Operation, a.parent_operation_id)
        if parent:
            parent.enabled_at = clock.now()
    activity.log(db, "execution.completed", actor=actor, entity="operation", entity_id=op_row.id, client_id=a.client_id,
                 details={"state": op_row.state, "verification_passed": op_row.verification_passed,
                          "resources": len(op_row.external_resource_names), "adapter": op_row.adapter_name},
                 integration_mode=op_row.integration_mode)
    db.commit()
    return op_row


def _post_execution_updates(db: Session, a: Approval, payload: dict, op_row: Operation) -> None:
    internal = payload.get("internal") or {}
    now = clock.now()
    for rn in op_row.external_resource_names:
        c = db.scalar(select(Campaign).where(Campaign.external_resource_name == rn))
        if c:
            c.created_by_app = c.created_by_app or a.approval_type == "CREATE"
            if internal.get("business_id"):
                c.business_id = internal["business_id"]
            if internal.get("weekly_conversion_target"):
                c.weekly_conversion_target = internal["weekly_conversion_target"]
            if internal.get("monthly_budget"):
                c.monthly_budget_micros = int(internal["monthly_budget"] * 1e6)
            if internal.get("services"):
                c.services_covered = internal["services"]
            c.last_changed_at = now
            if a.approval_type == "ENABLE":
                c.launched_at = c.launched_at or now
    for o in payload.get("operations", []):
        rn = o.get("resource_name")
        if not rn:
            continue
        for model in (Campaign, AdGroup, Ad):
            row = db.scalar(select(model).where(model.external_resource_name == rn))
            if row:
                row.last_changed_at = now
                if model is Campaign and a.approval_type == "ENABLE":
                    row.launched_at = row.launched_at or now


def execute_policy(db: Session, *, policy: str, client: Client, account: GoogleAdsAccount, operations: list[dict],
                   business_id: str | None, reason: str, evidence: dict) -> Operation:
    """Non-approval policy writes (AUTO_NEGATIVE / EMERGENCY). WriteGuard enforces the policy's own conditions."""
    safety.check(db, policy=policy, client=client, account=account, business_id=business_id)
    adapter = registry.get("google_ads")
    payload = {"kind": policy, "customer_id": account.customer_id, "operations": operations, "reason": reason, "evidence": evidence}
    key = hashlib.sha256(f"{policy}|{payload_hash(payload)}|{clock.today()}".encode()).hexdigest()
    dup = db.scalar(select(Operation).where(Operation.idempotency_key == key))
    if dup:
        return dup
    op_row = Operation(client_id=client.id, google_ads_account_id=account.id, policy=policy, idempotency_key=key,
                       tracking_label="gaa-" + key[:10], adapter_name=adapter.name,
                       integration_mode="REAL" if adapter.is_real else "MOCK", operation_type=policy,
                       request_payload=payload, payload_hash=payload_hash(payload), state="PENDING", attempts=1)
    db.add(op_row)
    db.commit()
    try:
        resp = adapter.mutate(account.customer_id, operations, request_id=key)
        op_row.external_response = redact(resp)
        op_row.external_resource_names = [r["resource_name"] for r in resp.get("results", [])]
        op_row.state = "SUCCEEDED"
        ok, diffs = verify_read_back(op_row)
        op_row.verification_passed, op_row.verification_diff = ok, diffs
    except AdapterTimeout as exc:
        op_row.state, op_row.error = "UNKNOWN", exc.message
        reconcile(db, op_row)
    except AdapterError as exc:
        op_row.state, op_row.error = "FAILED", f"{exc.kind}: {exc.message}"
    activity.log(db, f"execution.{policy.lower()}", entity="operation", entity_id=op_row.id, client_id=client.id,
                 details={"state": op_row.state, "reason": reason}, integration_mode=op_row.integration_mode)
    db.commit()
    return op_row


def serialize(o: Operation) -> dict:
    return {"id": o.id, "approval_id": o.approval_id, "policy": o.policy, "idempotency_key": o.idempotency_key,
            "adapter_name": o.adapter_name, "integration_mode": o.integration_mode, "operation_type": o.operation_type,
            "state": o.state, "external_resource_names": o.external_resource_names, "verification_passed": o.verification_passed,
            "verification_diff": o.verification_diff, "attempts": o.attempts, "error": o.error,
            "request_payload": o.request_payload, "external_response": o.external_response,
            "reconciled_at": o.reconciled_at.isoformat() if o.reconciled_at else None,
            "enabled_at": o.enabled_at.isoformat() if o.enabled_at else None, "created_at": o.created_at.isoformat()}


Conflict  # noqa: B018
