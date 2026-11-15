"""Google Ads account binding, end to end: link -> prepare -> approval bound to the exact customer -> execution re-checks ownership.

Regression for: an account shown as linked on the accounts page while the client's draft said "This draft has no Google Ads account yet".
Root causes covered: a draft prepared before the account was linked was never rebound; a business's stale account choice was followed
instead of the client's actual link; relinking an account did not invalidate approvals bound to it.
"""
from __future__ import annotations

import uuid

import pytest

from app.core.errors import SafetyBlocked
from app.db.session import session_scope
from app.models import Approval, Business, GoogleAdsAccount, MockCustomer, Task, User
from tests.fakes.mock_world.seed import MCC
from app.services import accounts_service, execution
from tests.conftest import ADMIN, SUPER, Api


def _client_and_business(admin: Api, name: str) -> tuple[str, str]:
    r = admin.post("/clients", {"name": name, "owner_contact": "o@bind.example", "backup_contact": "b@bind.example", "tag_access_contact": "t@bind.example"})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    r = admin.post(f"/clients/{cid}/businesses", {
        "name": f"{name} (business)", "business_type": "home_services", "website_url": "https://www.northsideplumbing.example/",
        "phone": "(555) 201-4400", "address": "1200 Example Ave, Columbus, OH", "verified_claims": ["Licensed & Insured"],
        "services": [{"name": "Drain Cleaning", "landing_page_url": "https://www.northsideplumbing.example/drain-cleaning"}],
        "competitors": [{"name": "Rapid Rooter Columbus", "website_url": "https://www.rapidrooter.example/"}]})
    assert r.status_code == 200, r.text
    return cid, r.json()["id"]


def _free_accounts(admin: Api, n: int) -> list[dict]:
    """Fresh, unlinked, non-manager accounts in the admin's workspace (the shared fake world's accounts are linked by other tests)."""
    seen = [a for a in admin.post("/ads-accounts/discover").json()]
    out = []
    with session_scope() as db:
        ws = db.get(GoogleAdsAccount, seen[0]["id"]).workspace_id if seen else None
        for _ in range(n):
            cid = str(uuid.uuid4().int)[:10].rjust(10, "7")
            a = GoogleAdsAccount(workspace_id=ws, customer_id=cid, descriptive_name=f"Binding test {cid}", is_manager=False, status="ENABLED")
            db.add(a)
            db.add(MockCustomer(customer_id=cid, descriptive_name=a.descriptive_name, manager_customer_id=MCC, auto_tagging_enabled=True))
            db.flush()
            out.append({"id": a.id, "customer_id": cid, "client_id": None, "is_manager": False})
    return out


def _build(admin: Api, cid: str, bid: str) -> dict:
    r = admin.post("/tasks?sync=true", {"task_type": "BUILD_CAMPAIGN", "client_id": cid, "business_id": bid,
                                        "fields": {"monthly_budget": 3000, "weekly_target": 1}})
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["status"] == "AWAITING_APPROVAL", (t["status"], t["error"])
    return t


def _execute(approval_id: str):
    with session_scope() as db:
        actor = db.query(User).filter(User.email == SUPER).one()
        execution.execute(db, actor, approval_id)


def test_draft_prepared_before_linking_explains_itself_and_binds_to_the_linked_account():
    admin = Api(ADMIN)
    cid, bid = _client_and_business(admin, f"Bind Co {uuid.uuid4().hex[:5]}")
    (acct,) = _free_accounts(admin, 1)
    t = _build(admin, cid, bid)  # prepared with no account linked: a plan, not deployable
    with session_scope() as db:
        assert db.get(Approval, t["approval_id"]).google_ads_account_id is None
    with pytest.raises(SafetyBlocked) as e:
        _execute(t["approval_id"])
    assert "No Google Ads account is linked to" in e.value.message and e.value.details["action"] == "link_account"

    assert admin.post(f"/ads-accounts/{acct['id']}/link", {"client_id": cid}).status_code == 200
    d = admin.get(f"/tasks/{t['id']}").json()["account_binding"]
    assert d["status"] == "linked" and d["customer_id"] == acct["customer_id"] and d["rebind_available"] is True and d["approval_customer_id"] is None
    with pytest.raises(SafetyBlocked) as e:  # the exact message the user saw is replaced by what is really wrong and what to do
        _execute(t["approval_id"])
    assert "prepared before" in e.value.message and e.value.details["action"] == "bind_account"

    r = admin.post(f"/tasks/{t['id']}/bind-account")
    assert r.status_code == 200, r.text
    t2 = r.json()
    assert t2["approval_id"] != t["approval_id"] and t2["account_binding"]["rebind_available"] is False
    assert t2["account_binding"]["approval_customer_id"] == acct["customer_id"]
    with session_scope() as db:
        new, old = db.get(Approval, t2["approval_id"]), db.get(Approval, t["approval_id"])
        assert new.google_ads_account_id == acct["id"] and new.payload["customer_id"] == acct["customer_id"]
        assert new.payload_version == old.payload_version + 1 and new.supersedes_id == old.id
        assert old.status == "INVALIDATED" and old.superseded_by_id == new.id
        assert new.status in ("PENDING_APPROVAL", "VALIDATION_FAILED")  # needs approval again; nothing was executed


def test_relinking_an_account_to_another_client_invalidates_its_approvals_and_execution_rechecks_ownership():
    admin = Api(ADMIN)
    cid, bid = _client_and_business(admin, f"Owner Co {uuid.uuid4().hex[:5]}")
    other, _ = _client_and_business(admin, f"Other Co {uuid.uuid4().hex[:5]}")
    (acct,) = _free_accounts(admin, 1)
    admin.post(f"/ads-accounts/{acct['id']}/link", {"client_id": cid})
    t = _build(admin, cid, bid)
    with session_scope() as db:
        assert db.get(Approval, t["approval_id"]).google_ads_account_id == acct["id"]
    admin.post(f"/ads-accounts/{acct['id']}/link", {"client_id": other})
    with session_scope() as db:
        assert db.get(Approval, t["approval_id"]).status == "INVALIDATED"
    # even an approval that somehow kept its status is refused: ownership is re-checked at execution
    with session_scope() as db:
        db.get(Approval, t["approval_id"]).status = "PENDING_APPROVAL"
    with pytest.raises(SafetyBlocked) as e:
        _execute(t["approval_id"])
    assert "no longer linked to this client" in e.value.message
    admin.post(f"/ads-accounts/{acct['id']}/link", {"client_id": None})


def test_one_source_of_truth_stale_choice_ignored_ambiguity_never_guessed():
    admin = Api(ADMIN)
    cid, bid = _client_and_business(admin, f"Truth Co {uuid.uuid4().hex[:5]}")
    a1, a2 = _free_accounts(admin, 2)
    admin.post(f"/ads-accounts/{a1['id']}/link", {"client_id": cid})
    with session_scope() as db:
        biz = db.get(Business, bid)
        biz.google_ads_account_id = a2["id"]  # a stale choice: an account NOT linked to this client
        b = accounts_service.deployment_binding(db, biz)
        assert b["status"] == "linked" and b["account"].id == a1["id"] and b["stale_selection"] is True
        assert "ignored" in b["message"]
    admin.post(f"/ads-accounts/{a2['id']}/link", {"client_id": cid})
    with session_scope() as db:
        biz = db.get(Business, bid)
        biz.google_ads_account_id = None
        b = accounts_service.deployment_binding(db, biz)
        assert b["status"] == "ambiguous" and b["account"] is None and len(b["candidates"]) == 2  # never guessed
        biz.google_ads_account_id = a2["id"]
        assert accounts_service.deployment_binding(db, biz)["account"].id == a2["id"]  # an explicit choice among linked accounts
        biz.google_ads_account_id = None
    for a in (a1, a2):
        admin.post(f"/ads-accounts/{a['id']}/link", {"client_id": None})


def test_a_manager_account_is_never_a_deployment_target():
    with session_scope() as db:
        mgr = db.query(GoogleAdsAccount).filter(GoogleAdsAccount.is_manager.is_(True)).first()
        if mgr is None:
            pytest.skip("no manager account in the fake world")
    r = Api(ADMIN).post(f"/ads-accounts/{mgr.id}/link", {"client_id": Api(ADMIN).get("/clients").json()[0]["id"]})
    assert r.status_code == 422


def test_unlinked_task_shows_how_to_link():
    admin = Api(ADMIN)
    cid, bid = _client_and_business(admin, f"Nolink Co {uuid.uuid4().hex[:5]}")
    t = _build(admin, cid, bid)
    d = admin.get(f"/tasks/{t['id']}").json()["account_binding"]
    assert d["status"] == "none" and "Google Ads accounts page" in d["message"] and d["rebind_available"] is False
    assert admin.post(f"/tasks/{t['id']}/bind-account").status_code == 422
    with session_scope() as db:
        assert db.get(Task, t["id"]).status == "AWAITING_APPROVAL"


def test_with_two_linked_accounts_the_user_chooses_and_only_a_linked_account_is_accepted():
    admin = Api(ADMIN)
    cid, bid = _client_and_business(admin, f"Choice Co {uuid.uuid4().hex[:5]}")
    t = _build(admin, cid, bid)
    a1, a2, stranger = _free_accounts(admin, 3)
    for a in (a1, a2):
        admin.post(f"/ads-accounts/{a['id']}/link", {"client_id": cid})
    d = admin.get(f"/tasks/{t['id']}").json()["account_binding"]
    assert d["status"] == "ambiguous" and d["choice_needed"] is True and d["rebind_available"] is False and len(d["candidates"]) == 2
    assert admin.post(f"/tasks/{t['id']}/bind-account").status_code == 422  # never guessed
    r = admin.post(f"/tasks/{t['id']}/bind-account", {"account_id": stranger["id"]})
    assert r.status_code == 422 and "not linked to this client" in r.text
    r = admin.post(f"/tasks/{t['id']}/bind-account", {"account_id": a2["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["account_binding"]["approval_customer_id"] == a2["customer_id"]
    with session_scope() as db:
        assert db.get(Business, bid).google_ads_account_id == a2["id"]
        db.get(Business, bid).google_ads_account_id = None
    for a in (a1, a2):
        admin.post(f"/ads-accounts/{a['id']}/link", {"client_id": None})
