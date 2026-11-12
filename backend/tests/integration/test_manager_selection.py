"""Regression: the manager saved on the Google Ads integration and the manager the Accounts page reads must be the same thing, and a
manager whose only linked account is another manager must say so instead of reporting a successful discovery of nothing.

The hierarchy used here mirrors what the real API returned for the reported case: root manager -> one sub-manager -> no client accounts.
"""
from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from app.db.session import session_scope
from app.models import GoogleAdsAccount, MockCustomer, Workspace
from app.services import integrations_service, mcc_service
from tests.conftest import MANAGER, SUPER, Api
from tests.integration.test_mcc_accounts import _adapter, _cc

ROOT, SUB, PLAIN = "4400000000", "5400000000", "6400000000"


@pytest.fixture
def nested_managers():
    """A manager whose only child is another manager; plus a regular account. Everything is put back afterwards."""
    with session_scope() as db:
        ws = mcc_service.default_workspace(db)
        before_ws = (ws.google_ads_manager_customer_id, ws.google_ads_manager_name, ws.discovery_status, ws.discovery_message, ws.accounts_discovered_at)
        row = integrations_service._row(db, "google_ads", create=True)
        before_settings = dict(row.settings or {})
        db.add_all([MockCustomer(customer_id=ROOT, descriptive_name="Root MCC", is_manager=True, accessible=True),
                    MockCustomer(customer_id=SUB, descriptive_name="Sub MCC", is_manager=True, manager_customer_id=ROOT, accessible=True),
                    MockCustomer(customer_id=PLAIN, descriptive_name="Plain account", is_manager=False, accessible=True)])
    integrations_service.invalidate()
    yield
    with session_scope() as db:
        ws = mcc_service.default_workspace(db)
        (ws.google_ads_manager_customer_id, ws.google_ads_manager_name, ws.discovery_status, ws.discovery_message, ws.accounts_discovered_at) = before_ws
        integrations_service._row(db, "google_ads", create=True).settings = before_settings
        db.execute(delete(MockCustomer).where(MockCustomer.customer_id.in_([ROOT, SUB, PLAIN])))
        db.execute(delete(GoogleAdsAccount).where(GoogleAdsAccount.customer_id.in_([ROOT, SUB, PLAIN])))
    integrations_service.invalidate()


def _legacy_state(manager: str):
    """The reported state: the manager ID lives only in the integration settings; the workspace field is empty."""
    with session_scope() as db:
        ws = mcc_service.default_workspace(db)
        ws.google_ads_manager_customer_id, ws.google_ads_manager_name, ws.discovery_status, ws.discovery_message = "", "", "NEVER_RUN", ""
        row = integrations_service._row(db, "google_ads", create=True)
        row.settings = {**(row.settings or {}), "login_customer_id": manager}
    integrations_service.invalidate()


def test_manager_saved_on_the_integration_is_the_selected_manager_everywhere(nested_managers):
    _legacy_state(ROOT)
    sup = Api(SUPER)
    status = sup.get("/google-ads/status").json()
    assert status["manager_customer_id"] == ROOT and status["manager_source"] == "integration_setting"  # was None: "no manager selected"
    assert sup.get("/google-ads/accounts").json()["manager_customer_id"] == ROOT
    cands = sup.get("/google-ads/managers").json()
    assert cands["selected_manager"] == ROOT and [c["customer_id"] for c in cands["candidates"] if c["selected"]] == [ROOT]


def test_refresh_uses_that_manager_stores_it_and_survives_a_reload(nested_managers):
    _legacy_state(ROOT)
    sup = Api(SUPER)
    out = sup.post("/google-ads/accounts/refresh").json()
    assert out["manager_customer_id"] == ROOT and out["manager_name"] == "Root MCC"
    with session_scope() as db:  # committed on the workspace, so nothing depends on the fallback any more
        ws = db.scalar(select(Workspace).where(Workspace.slug == "default"))
        assert ws.google_ads_manager_customer_id == ROOT and ws.accounts_discovered_at is not None
    again = Api(SUPER).get("/google-ads/status").json()  # a fresh session = a page reload
    assert again["manager_customer_id"] == ROOT and again["manager_source"] == "workspace" and again["discovery_status"] == "NO_CLIENTS"


def test_a_manager_with_only_a_sub_manager_reports_no_clients_with_the_real_reason(nested_managers):
    _legacy_state(ROOT)
    sup = Api(SUPER)
    out = sup.post("/google-ads/accounts/refresh").json()
    assert out["found"] == 2 and out["client_accounts_found"] == 0 and out["sub_managers_found"] == 1
    assert out["discovery_status"] == "NO_CLIENTS" and out["errors"] == []
    assert "no client accounts" in out["no_clients_reason"] and SUB in out["no_clients_reason"] and "Sub MCC" in out["no_clients_reason"]
    assert out["discovery_message"] == out["no_clients_reason"]  # persisted, so the reason is still shown after a reload
    listing = sup.get("/google-ads/accounts").json()
    assert all(a["customer_id"] not in (ROOT, SUB) for a in listing["items"])  # managers never appear as client accounts
    managers = {m["customer_id"]: m for m in listing["manager_accounts"]}
    assert managers[SUB]["parent_customer_id"] == ROOT and managers[SUB]["level"] == 1 and managers[ROOT]["level"] == 0
    assert not managers[SUB]["selectable"] and managers[SUB]["is_manager"]
    with session_scope() as db:  # persisted and linked to the manager they were actually found under
        rows = {a.customer_id: a for a in db.scalars(select(GoogleAdsAccount).where(GoogleAdsAccount.customer_id.in_([ROOT, SUB])))}
        assert rows[SUB].is_manager and rows[SUB].login_customer_id == ROOT and rows[SUB].parent_customer_id == ROOT
        assert rows[ROOT].is_manager and rows[ROOT].workspace_id == rows[SUB].workspace_id


def test_a_regular_account_saved_as_the_manager_is_refused_with_the_reason(nested_managers):
    _legacy_state(PLAIN)
    sup = Api(SUPER)
    r = sup.post("/google-ads/accounts/refresh")
    assert r.status_code == 422 and "not a manager" in r.text
    st = sup.get("/google-ads/status").json()
    assert st["discovery_status"] == "FAILED" and "not a manager" in st["discovery_message"]
    with session_scope() as db:
        assert db.scalar(select(Workspace.google_ads_manager_customer_id).where(Workspace.slug == "default")) == ""  # never adopted


def test_saving_a_new_manager_id_on_the_integration_updates_the_stored_selection(nested_managers):
    _legacy_state(ROOT)
    Api(SUPER).post("/google-ads/accounts/refresh")
    with session_scope() as db:
        integrations_service.save(db, "google_ads", settings={"login_customer_id": "540-000-0000"}, secrets=None, user_id=None)
    integrations_service.invalidate()
    st = Api(SUPER).get("/google-ads/status").json()
    assert st["manager_customer_id"] == SUB and st["discovery_status"] == "NEVER_RUN"  # the two stores cannot drift apart


def test_the_explicit_choice_wins_over_the_integration_setting(nested_managers):
    _legacy_state(ROOT)
    sup = Api(SUPER)
    assert sup.c.put("/api/v1/google-ads/manager", json={"customer_id": SUB}, headers=sup._h()).status_code == 200
    assert sup.get("/google-ads/status").json()["manager_customer_id"] == SUB
    with session_scope() as db:
        assert mcc_service.effective_manager(db) == {"customer_id": SUB, "name": "Sub MCC", "source": "workspace"}


def test_manager_accounts_cannot_be_linked_to_a_client(nested_managers):
    _legacy_state(ROOT)
    sup = Api(SUPER)
    sup.post("/google-ads/accounts/refresh")
    rows = {a["customer_id"]: a for a in sup.get("/ads-accounts").json()}
    client_id = Api(MANAGER).get("/clients").json()[0]["id"]
    r = sup.post(f"/ads-accounts/{rows[SUB]['id']}/link", json={"client_id": client_id})
    assert r.status_code == 422 and "manager" in r.text.lower()


def test_real_adapter_walks_into_the_sub_manager_and_keeps_manager_flags():
    """Same shape as the real customer_client rows: the root returns itself and one manager; that manager returns only itself."""
    tree = {ROOT: [[_cc(ROOT, "Root MCC", manager=True, level=0), _cc(SUB, "Sub MCC", manager=True, level=1)]],
            SUB: [[_cc(SUB, "Sub MCC", manager=True, level=0)]]}
    a, ads = _adapter(tree, accessible=(ROOT, SUB))
    res = a.list_customer_clients(ROOT)
    assert ads.calls == [ROOT, SUB] and res["errors"] == []  # the sub-manager was queried, through the root
    by = {x["customer_id"]: x for x in res["accounts"]}
    assert set(by) == {ROOT, SUB} and all(x["is_manager"] for x in by.values())
    assert by[SUB]["parent_customer_id"] == ROOT and by[SUB]["level"] == 1 and by[ROOT]["level"] == 0
