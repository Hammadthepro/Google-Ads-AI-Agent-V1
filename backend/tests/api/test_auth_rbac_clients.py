"""#1 dev login, #2 unauthorized blocked, #3 role permissions, #4 create client, #5 create business, Google OIDC verification."""
from __future__ import annotations

import pytest

from app.adapters.auth import GoogleOIDCAuthenticationProvider
from app.core.errors import Unauthorized
from tests.conftest import ADMIN, ANALYST, MANAGER, SUPER, Api


def test_dev_login_and_me():
    api = Api(MANAGER)
    me = api.get("/auth/me").json()
    assert me["user"]["email"] == MANAGER and me["user"]["role"] == "CAMPAIGN_MANAGER"
    assert "approvals.decide" not in me["user"]["permissions"]


def test_unauthorized_blocked():
    anon = Api()
    assert anon.get("/clients").status_code == 401
    assert anon.get("/approvals").status_code == 401
    r = anon.c.post("/api/v1/auth/dev-login", json={"email": "stranger@evil.example"})
    assert r.status_code == 401 and "invited" in r.text


def test_deactivated_user_loses_access(db):
    sup = Api(SUPER)
    r = sup.post("/users", {"email": "temp.user@napollo.example", "name": "Temp", "role": "ANALYST"})
    assert r.status_code == 200
    uid = r.json()["id"]
    temp = Api("temp.user@napollo.example")
    assert temp.get("/auth/me").status_code == 200
    assert sup.patch(f"/users/{uid}", {"is_active": False}).status_code == 200
    assert temp.get("/auth/me").status_code == 401  # sessions revoked immediately
    assert Api().c.post("/api/v1/auth/dev-login", json={"email": "temp.user@napollo.example"}).status_code == 401


def test_role_based_permissions_matrix():
    sup, admin, mgr, ana = Api(SUPER), Api(ADMIN), Api(MANAGER), Api(ANALYST)
    # Analyst: read-only
    assert ana.get("/clients").status_code == 200
    assert ana.post("/clients", {"name": "x"}).status_code == 403
    clients = ana.get("/clients").json()
    cid = clients[0]["id"]
    assert ana.post("/tasks", {"task_type": "CHANGE_BUDGET", "client_id": cid}).status_code == 403
    pending = admin.get("/approvals?status=PENDING_APPROVAL").json()
    assert pending
    assert ana.post(f"/approvals/{pending[0]['id']}/approve", {"payload_hash": "x"}).status_code == 403
    assert ana.patch("/settings", {"key": "wasted.min_clicks", "value": 5}).status_code == 403
    # Campaign manager: cannot approve, cannot manage users, only assigned clients
    assert mgr.post(f"/approvals/{pending[0]['id']}/approve", {"payload_hash": "x"}).status_code == 403
    assert mgr.get("/users").status_code == 403
    names = {c["name"] for c in mgr.get("/clients").json()}
    assert "Summit Legal Partners" not in names and "Northside Home Services" in names
    summit = next(c for c in admin.get("/clients").json() if c["name"] == "Summit Legal Partners")
    assert mgr.get(f"/clients/{summit['id']}").status_code == 403
    # Admin: cannot change system settings or authorize real writes; cannot create super admins
    assert admin.patch("/settings", {"key": "wasted.min_clicks", "value": 5}).status_code == 403
    assert admin.post("/users", {"email": "x2@napollo.example", "role": "SUPER_ADMIN"}).status_code == 403
    assert admin.post("/kill-switches", {"scope": "GLOBAL", "is_on": True}).status_code == 403
    # Super Admin: can
    assert sup.patch("/settings", {"key": "wasted.min_clicks", "value": 10}).status_code == 200
    assert sup.patch("/settings", {"key": "real_writes_authorized", "value": True, "reason": "short"}).status_code == 422


def test_create_client_and_business_with_validation():
    admin = Api(ADMIN)
    r = admin.post("/clients", {"name": "Acme Roofing (test)", "owner_contact": "a@b.example"})
    assert r.status_code == 200
    cid = r.json()["id"]
    assert admin.post("/clients", {"name": "  "}).status_code == 422
    base = {"name": "Acme Roofing", "website_url": "https://acme-roof.example/", "phone": "(555) 111-2222",
            "services": [{"name": "Roof Repair"}, {"name": "Gutters", "is_excluded": True}]}
    r = admin.post(f"/clients/{cid}/businesses", {**base, "competitors": [{"name": f"C{i}"} for i in range(4)]})
    assert r.status_code == 422 and "three" in r.text
    r = admin.post(f"/clients/{cid}/businesses", {**base, "website_url": "javascript:alert(1)"})
    assert r.status_code == 422
    r = admin.post(f"/clients/{cid}/businesses", {**base, "competitors": [{"name": "Rival Roofing", "website_url": "https://rival.example/"}]})
    assert r.status_code == 200
    b = r.json()
    assert {s["name"]: s["is_excluded"] for s in b["services"]} == {"Roof Repair": False, "Gutters": True}
    detail = admin.get(f"/clients/{cid}").json()
    assert detail["businesses"][0]["id"] == b["id"]
    acts = admin.get(f"/activity?client_id={cid}").json()
    assert {"client.created", "business.created"} <= {a["action"] for a in acts}


def _claims(**over):
    base = {"iss": "https://accounts.google.com", "aud": "client-123", "email": "admin@napollo.example", "email_verified": True,
            "sub": "1234567890", "name": "Alex"}
    return {**base, **over}


def test_google_oidc_verification(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-123")
    monkeypatch.setenv("ALLOWED_LOGIN_DOMAINS", "napollo.example")
    from app.core.config import reset_settings_cache
    reset_settings_cache()
    try:
        ok = GoogleOIDCAuthenticationProvider(verifier=lambda t, aud: _claims())
        assert ok.verify({"id_token": "tok"})["email"] == "admin@napollo.example"
        for bad in (_claims(aud="other"), _claims(iss="evil.com"), _claims(email_verified=False), _claims(email="x@gmail.com")):
            with pytest.raises(Unauthorized):
                GoogleOIDCAuthenticationProvider(verifier=lambda t, aud, b=bad: b).verify({"id_token": "tok"})
        def boom(t, aud):
            raise ValueError("bad signature")
        with pytest.raises(Unauthorized):
            GoogleOIDCAuthenticationProvider(verifier=boom).verify({"id_token": "tok"})
        with pytest.raises(Unauthorized):
            ok.verify({})
    finally:
        reset_settings_cache()
