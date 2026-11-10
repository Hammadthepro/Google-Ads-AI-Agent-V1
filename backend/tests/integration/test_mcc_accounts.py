"""MCC-based multi-client account management: manager selection, hierarchy discovery, persistence, selection and switching.

Two kinds of tests, kept apart on purpose:
  * RealGoogleAdsAdapter tests drive the real adapter code with a stand-in Google *client object* (isolated unit fixtures);
  * service/API tests use the test fakes installed by conftest (never imported by the app).
Checks against the real Google Ads API are in backend/live_tests.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest
from sqlalchemy import select

from app.adapters import registry
from app.adapters.base import AuthError
from app.adapters.google_ads.real import RealGoogleAdsAdapter, error_code
from app.core.errors import IntegrationNotConfigured, ValidationFailed
from app.db.session import session_scope
from app.models import GoogleAdsAccount, UserAccountSelection, Workspace
from app.services import mcc_service
from tests.conftest import ADMIN, MANAGER, SUPER, Api


# --------------------------------------------------------------------------- real adapter against a stand-in Google client
def _cc(id_, name, status="ENABLED", manager=False, level=0, test=False):
    return NS(id=id_, descriptive_name=name, status=status, manager=manager, level=level, currency_code="USD", time_zone="America/Chicago",
              test_account=test, hidden=False)


class _Ads:
    """GoogleAdsService stand-in: `tree` maps manager id -> list of batches (each batch a list of customer_client rows)."""

    def __init__(self, tree, fail=None):
        self.tree, self.fail, self.calls = tree, fail or {}, []

    def search_stream(self, customer_id, query):
        self.calls.append(customer_id)
        if customer_id in self.fail:
            raise Exception(self.fail[customer_id])
        for batch in self.tree.get(customer_id, []):
            yield NS(results=[NS(customer_client=row, customer=row) for row in batch])


class _Cust:
    def __init__(self, names):
        self.names = names

    def list_accessible_customers(self):
        return NS(resource_names=[f"customers/{n}" for n in self.names])


def _adapter(tree, accessible=("1000000000",), fail=None):
    ads, cust = _Ads(tree, fail), _Cust(accessible)
    client = NS(get_service=lambda name: cust if name == "CustomerService" else ads)
    a = RealGoogleAdsAdapter(client_factory=lambda: client)
    a._sleep = lambda s: None
    return a, ads


HIERARCHY = {
    "1000000000": [[_cc("1000000000", "Agency MCC", manager=True, level=0), _cc("2000000001", "Client A", level=1)],  # batch 1
                   [_cc("2000000002", "Client B", status="SUSPENDED", level=1), _cc("3000000000", "Regional MCC", manager=True, level=1)],  # batch 2
                   [_cc("2000000003", "Old Client", status="CANCELED", level=1), _cc("3999999999", "Closed MCC", status="CLOSED", manager=True, level=1)]],
    "3000000000": [[_cc("3000000000", "Regional MCC", manager=True, level=0), _cc("4000000001", "Client C (via regional)", level=1)]],
}


def test_customer_client_hierarchy_follows_sub_managers_and_every_result_batch():
    a, ads = _adapter(HIERARCHY)
    res = a.list_customer_clients("100-000-0000")  # dashes are accepted, the API gets digits
    by = {x["customer_id"]: x for x in res["accounts"]}
    assert set(by) == {"1000000000", "2000000001", "2000000002", "3000000000", "2000000003", "3999999999", "4000000001"}  # all 3 batches read
    assert by["4000000001"]["parent_customer_id"] == "3000000000" and by["4000000001"]["level"] == 2  # grandchild: parent + depth known
    assert by["2000000001"]["parent_customer_id"] == "1000000000" and by["2000000001"]["level"] == 1
    assert by["2000000002"]["status"] == "SUSPENDED" and by["2000000003"]["status"] == "CANCELED"
    assert by["3000000000"]["is_manager"] and by["1000000000"]["level"] == 0
    assert "3999999999" not in ads.calls, "a CLOSED manager is listed but never walked into"
    assert res["errors"] == []


def test_unreadable_branch_is_reported_with_the_google_error_code_not_invented():
    fail = {"3000000000": "403 PERMISSION_DENIED authorization_error: USER_PERMISSION_DENIED"}
    a, _ = _adapter(HIERARCHY, fail=fail)
    res = a.list_customer_clients("1000000000")
    assert res["errors"] == [{"customer_id": "3000000000", "code": "USER_PERMISSION_DENIED"}]
    ids = {x["customer_id"] for x in res["accounts"]}
    assert "2000000001" in ids and "4000000001" not in ids  # the rest was read; the unreadable subtree is simply absent
    assert error_code("... authorizationError: CUSTOMER_NOT_ENABLED ...") == "CUSTOMER_NOT_ENABLED"
    assert error_code("RESOURCE_EXHAUSTED") == "RESOURCE_EXHAUSTED" and error_code("nothing here") == ""


def test_manager_candidates_describe_each_accessible_account_and_capture_per_account_errors():
    tree = {"1000000000": [[_cc("1000000000", "Agency MCC", manager=True)]],
            "9000000000": [[_cc("9000000000", "Closed test MCC", status="CLOSED", manager=True, test=True)]]}
    a, _ = _adapter(tree, accessible=("1000000000", "9000000000", "7000000000"),
                    fail={"7000000000": "authorizationError: CUSTOMER_NOT_ENABLED"})
    got = {c["customer_id"]: c for c in a.list_manager_candidates()}
    assert got["1000000000"]["is_manager"] and got["1000000000"]["status"] == "ENABLED"
    assert got["9000000000"]["status"] == "CLOSED" and got["9000000000"]["test_account"] is True
    assert got["7000000000"]["error"] == "CUSTOMER_NOT_ENABLED" and got["7000000000"]["is_manager"] is None


def test_expired_oauth_is_a_safe_auth_error_and_flags_the_integration():
    a, _ = _adapter(HIERARCHY, fail={"1000000000": "invalid_grant: Token has been expired or revoked."})
    with pytest.raises(AuthError) as exc:
        a.list_customer_clients("1000000000")
    assert "Reconnect Google Ads" in exc.value.message and "invalid_grant" not in exc.value.message


# --------------------------------------------------------------------------- service with the installed test fake
@pytest.fixture
def ws_reset():
    """Leave the shared seeded workspace as found."""
    with session_scope() as db:
        ws = mcc_service.default_workspace(db)
        before = (ws.google_ads_manager_customer_id, ws.google_ads_manager_name)
        sels = [(s.user_id, s.customer_id) for s in db.scalars(select(UserAccountSelection))]
    yield
    with session_scope() as db:
        ws = mcc_service.default_workspace(db)
        ws.google_ads_manager_customer_id, ws.google_ads_manager_name = before
        db.query(UserAccountSelection).delete()
        for uid, cid in sels:
            db.add(UserAccountSelection(user_id=uid, workspace_id=ws.id, customer_id=cid))
        for a in db.scalars(select(GoogleAdsAccount)):
            a.discovery_state, a.discovery_error, a.status = "ACTIVE", "", "ENABLED"


def test_manager_selection_and_discovery_persist_the_hierarchy(ws_reset):
    sup = Api(SUPER)
    cands = sup.get("/google-ads/managers").json()
    assert [m["customer_id"] for m in cands["managers"]] == ["1000000000"]
    assert sup.c.put("/api/v1/google-ads/manager", json={"customer_id": "111-000-0001"}, headers=sup._h()).status_code == 422  # a client, not a manager
    assert sup.c.put("/api/v1/google-ads/manager", json={"customer_id": "999-999-9999"}, headers=sup._h()).status_code == 404  # not accessible
    r = sup.c.put("/api/v1/google-ads/manager", json={"customer_id": "100-000-0000"}, headers=sup._h())
    assert r.status_code == 200 and r.json()["manager_customer_id"] == "1000000000"  # API format: digits only
    out = sup.post("/google-ads/accounts/refresh").json()
    assert out["found"] >= 5 and out["discovery_status"] == "OK" and out["errors"] == []
    listing = sup.get("/google-ads/accounts").json()
    names = {a["customer_id"]: a for a in listing["items"]}
    assert "1110000001" in names and names["1110000001"]["status"] == "ENABLED" and names["1110000001"]["customer_id_display"] == "111-000-0001"
    assert names["1110000001"]["manager_customer_id"] == "1000000000" and names["1110000001"]["parent_customer_id"] == "1000000000"
    assert "1000000000" not in names  # managers are hidden unless asked for
    assert any(a["is_manager"] for a in sup.get("/google-ads/accounts?include_managers=true").json()["items"])
    with session_scope() as db:  # persisted in the database, in the workspace, in the stored (digits-only) format
        rows = list(db.scalars(select(GoogleAdsAccount).where(GoogleAdsAccount.customer_id == "1110000001")))
        assert len(rows) == 1 and rows[0].workspace_id == mcc_service.default_workspace(db).id and rows[0].login_customer_id == "1000000000"


def test_only_super_admin_picks_the_manager_and_regular_users_cannot_refresh(ws_reset):
    assert Api(MANAGER).c.put("/api/v1/google-ads/manager", json={"customer_id": "1000000000"}, headers=Api(MANAGER)._h()).status_code in (401, 403)
    assert Api(ADMIN).get("/google-ads/managers").status_code == 403
    assert Api("analyst@napollo.example").post("/google-ads/accounts/refresh").status_code == 403
    sup = Api(SUPER)
    with session_scope() as db:
        mcc_service.default_workspace(db).google_ads_manager_customer_id = ""
    assert sup.post("/google-ads/accounts/refresh").status_code == 422  # a manager must be chosen first


def test_closed_suspended_and_inaccessible_accounts_are_listed_but_not_selectable(ws_reset):
    sup = Api(SUPER)
    fake = registry.get("google_ads")
    fake._knobs()
    fake.status_overrides.update({"1110000002": "CLOSED", "1110000003": "SUSPENDED"})
    sup.c.put("/api/v1/google-ads/manager", json={"customer_id": "1000000000"}, headers=sup._h())
    out = sup.post("/google-ads/accounts/refresh").json()
    assert out["by_status"]["CLOSED"] == 1 and out["by_status"]["SUSPENDED"] == 1
    items = {a["customer_id"]: a for a in sup.get("/google-ads/accounts").json()["items"]}
    assert items["1110000002"]["selectable"] is False and "closed" in items["1110000002"]["unavailable_reason"]
    assert items["1110000003"]["selectable"] is False
    r = sup.c.put("/api/v1/google-ads/selection", json={"customer_id": "1110000002"}, headers=sup._h())
    assert r.status_code == 422 and "closed" in r.text
    only = sup.get("/google-ads/accounts?only_selectable=true").json()["items"]
    assert all(a["selectable"] for a in only) and "1110000002" not in {a["customer_id"] for a in only}
    assert [a["customer_id"] for a in sup.get("/google-ads/accounts?status=CLOSED").json()["items"]] == ["1110000002"]


def test_partial_discovery_records_branch_errors_and_never_marks_accounts_missing(ws_reset):
    sup = Api(SUPER)
    sup.c.put("/api/v1/google-ads/manager", json={"customer_id": "1000000000"}, headers=sup._h())
    sup.post("/google-ads/accounts/refresh")
    fake = registry.get("google_ads")
    fake._knobs()
    fake.inaccessible["1000000000"] = "USER_PERMISSION_DENIED"
    out = sup.post("/google-ads/accounts/refresh").json()
    assert out["discovery_status"] == "PARTIAL" and out["errors"][0]["code"] == "USER_PERMISSION_DENIED"
    assert all(a["discovery_state"] == "ACTIVE" for a in sup.get("/google-ads/accounts").json()["items"])


def test_search_filter_by_name_and_customer_id_with_or_without_dashes(ws_reset):
    sup = Api(SUPER)
    sup.c.put("/api/v1/google-ads/manager", json={"customer_id": "1000000000"}, headers=sup._h())
    sup.post("/google-ads/accounts/refresh")
    assert [a["customer_id"] for a in sup.get("/google-ads/accounts?q=brightsmile").json()["items"]] == ["1110000002"]
    assert [a["customer_id"] for a in sup.get("/google-ads/accounts?q=111-000-0003").json()["items"]] == ["1110000003"]
    assert [a["customer_id"] for a in sup.get("/google-ads/accounts?q=1110000001").json()["items"]] == ["1110000001"]
    assert sup.get("/google-ads/accounts?q=zzzz-no-such").json()["total"] == 0


def test_selection_is_stored_per_user_survives_refresh_and_switching_works(ws_reset):
    sup, adm = Api(SUPER), Api(ADMIN)
    sup.c.put("/api/v1/google-ads/manager", json={"customer_id": "1000000000"}, headers=sup._h())
    sup.post("/google-ads/accounts/refresh")
    assert sup.get("/google-ads/selection").json()["selected"] is None
    r = sup.c.put("/api/v1/google-ads/selection", json={"customer_id": "111-000-0001"}, headers=sup._h())
    assert r.status_code == 200 and r.json()["selected"]["customer_id"] == "1110000001"
    adm.c.put("/api/v1/google-ads/selection", json={"customer_id": "1110000002"}, headers=adm._h())
    assert sup.get("/google-ads/selection").json()["selected"]["customer_id"] == "1110000001"  # per user
    assert adm.get("/google-ads/selection").json()["selected"]["customer_id"] == "1110000002"
    sup.post("/google-ads/accounts/refresh")  # refresh keeps previous valid selections
    assert sup.get("/google-ads/selection").json()["selected"]["customer_id"] == "1110000001"
    sw = sup.c.put("/api/v1/google-ads/selection", json={"customer_id": "1110000003"}, headers=sup._h()).json()  # switch, no .env involved
    assert sw["selected"]["customer_id"] == "1110000003"
    with session_scope() as db:
        assert db.query(UserAccountSelection).filter_by(customer_id="1110000003").count() == 1
        assert db.query(UserAccountSelection).count() == 2  # one row per user and workspace, updated in place
    assert sup.c.put("/api/v1/google-ads/selection", json={"customer_id": "123"}, headers=sup._h()).status_code == 422  # not a customer ID
    assert sup.c.put("/api/v1/google-ads/selection", json={"customer_id": "9999999999"}, headers=sup._h()).status_code == 404


def test_account_that_disappears_is_kept_marked_not_seen_and_selection_flags_it(ws_reset):
    sup = Api(SUPER)
    sup.c.put("/api/v1/google-ads/manager", json={"customer_id": "1000000000"}, headers=sup._h())
    sup.post("/google-ads/accounts/refresh")
    sup.c.put("/api/v1/google-ads/selection", json={"customer_id": "1110000001"}, headers=sup._h())
    fake = registry.get("google_ads")
    original = fake.list_customer_clients

    def without_one(mgr):
        res = original(mgr)
        res["accounts"] = [a for a in res["accounts"] if a["customer_id"] != "1110000001"]
        return res
    fake.list_customer_clients = without_one
    sup.post("/google-ads/accounts/refresh")
    sel = sup.get("/google-ads/selection").json()["selected"]
    assert sel["customer_id"] == "1110000001" and sel["selectable"] is False and sel["discovery_state"] == "NOT_SEEN"
    with session_scope() as db:
        assert db.scalar(select(GoogleAdsAccount.id).where(GoogleAdsAccount.customer_id == "1110000001")) is not None  # never deleted


def test_selecting_does_not_change_runtime_safety_or_write_anything():
    from app.core.config import get_settings
    s = get_settings()
    assert s.GOOGLE_ADS_WRITES_ENABLED is False and s.AUTO_NEGATIVES_MODE == "shadow" and s.EMERGENCY_BRAKE_WRITE_MODE == "simulate"


def test_future_team_support_accounts_belong_to_a_workspace():
    with session_scope() as db:
        ws = mcc_service.default_workspace(db)
        assert ws.slug == "default"
        assert db.scalar(select(GoogleAdsAccount).where(GoogleAdsAccount.workspace_id.is_(None))) is None  # none are orphaned
        # the one place user-to-account restrictions will be applied later:
        assert "workspace_id" in str(mcc_service.accessible_accounts(db, None).compile())


def test_customer_id_normalisation():
    assert mcc_service.normalize_customer_id(" 123-456-7890 ") == "1234567890"
    for bad in ("", "12345", "abc-def-ghij", "12345678901"):
        with pytest.raises(ValidationFailed):
            mcc_service.normalize_customer_id(bad)


# --------------------------------------------------------------------------- missing / expired credentials through the API
@pytest.mark.real_registry
def test_without_a_google_connection_every_account_endpoint_says_to_configure_it():
    sup = Api(SUPER)
    for method, path in (("get", "/google-ads/managers"), ("post", "/google-ads/accounts/refresh")):
        r = getattr(sup, method)(path)
        assert r.status_code == 424 and r.json()["error"]["code"] == "integration_not_configured", (path, r.text)
    with pytest.raises(IntegrationNotConfigured):
        registry.get("google_ads")


def test_expired_authorization_surfaces_as_a_safe_424_on_refresh(ws_reset):
    sup = Api(SUPER)
    sup.c.put("/api/v1/google-ads/manager", json={"customer_id": "1000000000"}, headers=sup._h())
    from tests.conftest import inject
    inject("google_ads", "auth_failure", "list_customer_clients", times=1)
    r = sup.post("/google-ads/accounts/refresh")
    assert r.status_code == 424 and r.json()["error"]["code"] == "authorization_failed"
    with session_scope() as db:
        assert db.scalar(select(Workspace.discovery_status).where(Workspace.slug == "default")) == "FAILED"
    assert sup.post("/google-ads/accounts/refresh").status_code == 200  # retry works once authorization is fixed
