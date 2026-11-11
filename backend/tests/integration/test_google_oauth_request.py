"""The Google OAuth authorization request and its callback: exact parameters, encoding, redirect URI precedence, state and code exchange security."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.core.config import reset_settings_cache
from app.core.errors import ValidationFailed
from app.db.session import session_scope
from app.models import ActivityLog, IntegrationConfig, OAuthState
from app.services import integrations_service as svc
from tests.conftest import ADMIN, SUPER, Api

CLIENT_ID = "609437209123-3c3h2iv1ha6mfllumb73gl3vbi4pc78r.apps.googleusercontent.com"
SECRET = "GOCSPX-test-secret-0123456789"
CODESPACES = "https://didactic-broccoli-v6wqgp57vq9x2pvvx-5173.app.github.dev/api/v1/integrations/google/callback"


@pytest.fixture(autouse=True)
def _ads_client(monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_ADS_CLIENT_SECRET", SECRET)
    monkeypatch.delenv("GOOGLE_ADS_REDIRECT_URI", raising=False)
    reset_settings_cache()
    svc.invalidate()
    yield
    with session_scope() as db:
        db.query(OAuthState).delete()
        db.query(IntegrationConfig).delete()
    monkeypatch.undo()
    reset_settings_cache()
    svc.invalidate()


def _start(who=SUPER, providers=("google_ads",), headers=None):
    api = Api(who)
    h = {**api._h(), **(headers or {})}
    return api, api.c.post("/api/v1/integrations/google/start", json={"providers": list(providers)}, headers=h)


def _q(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query, keep_blank_values=True).items()}


# --------------------------------------------------------------------------- the request itself
def test_authorization_url_has_every_required_parameter_correctly_encoded(monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_REDIRECT_URI", CODESPACES)
    reset_settings_cache()
    _, r = _start()
    assert r.status_code == 200
    url = r.json()["authorization_url"]
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
    q = _q(url)
    assert q["response_type"] == "code"  # the parameter Google reported missing
    assert q["client_id"] == CLIENT_ID
    assert q["redirect_uri"] == CODESPACES  # exactly GOOGLE_ADS_REDIRECT_URI
    assert q["access_type"] == "offline" and q["prompt"] == "consent"
    assert "https://www.googleapis.com/auth/adwords" in q["scope"].split()
    assert len(q["state"]) >= 32 and q["code_challenge_method"] == "S256" and q["code_challenge"]
    # percent-encoded: the redirect URI's ':' and '/' and the scope separator must not appear raw in the query string
    assert "redirect_uri=https%3A%2F%2Fdidactic-broccoli" in url and "scope=" in url and " " not in url
    assert url.count("response_type=code") == 1 and url.count("?") == 1
    assert [k for k in q] == ["client_id", "redirect_uri", "response_type", "scope", "access_type", "prompt", "state", "code_challenge",
                              "code_challenge_method", "include_granted_scopes"]


def test_redirect_uri_comes_from_the_environment_then_the_browsers_address_then_localhost(monkeypatch):
    _, r = _start(headers={"Origin": "https://forwarded-5173.app.github.dev"})
    assert _q(r.json()["authorization_url"])["redirect_uri"] == "https://forwarded-5173.app.github.dev/api/v1/integrations/google/callback"
    assert r.json()["redirect_uri_source"] == "the address of this page"
    monkeypatch.setenv("GOOGLE_ADS_REDIRECT_URI", CODESPACES)
    reset_settings_cache()
    _, r = _start(headers={"Origin": "https://forwarded-5173.app.github.dev"})
    assert _q(r.json()["authorization_url"])["redirect_uri"] == CODESPACES  # the environment always wins over the browser's address
    assert "GOOGLE_ADS_REDIRECT_URI" in r.json()["redirect_uri_source"]
    assert svc.oauth_redirect_uri("")[0] == CODESPACES
    monkeypatch.delenv("GOOGLE_ADS_REDIRECT_URI")
    reset_settings_cache()
    assert svc.oauth_redirect_uri("")[0] == svc.LOCAL_REDIRECT and svc.oauth_redirect_uri("javascript:alert(1)")[0] == svc.LOCAL_REDIRECT  # junk origins are ignored


def test_scopes_per_integration():
    _, r = _start(providers=("ga4",))
    assert r.status_code == 200, r.text
    assert set(_q(r.json()["authorization_url"])["scope"].split()) == {"openid", "email", "https://www.googleapis.com/auth/analytics.readonly"}
    _, r = _start(providers=("google_ads", "gbp"))
    assert {"https://www.googleapis.com/auth/adwords", "https://www.googleapis.com/auth/business.manage"} <= set(_q(r.json()["authorization_url"])["scope"].split())
    _, r = _start(providers=("groq",))
    assert r.status_code == 422  # not a Google OAuth integration


@pytest.mark.parametrize("bad", ["not a url", "/relative/callback", "https://host/cb?x=1", "https://host/cb#frag", "http://example.com/cb", "https://ho st/cb", "ftp://host/cb"])
def test_invalid_redirect_uris_are_refused_before_anything_is_sent(bad, monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_REDIRECT_URI", bad)
    reset_settings_cache()
    _, r = _start()
    assert r.status_code == 422 and "GOOGLE_ADS_REDIRECT_URI" in r.json()["error"]["message"] or "http redirect" in r.json()["error"]["message"]
    with session_scope() as db:
        assert db.query(OAuthState).count() == 0  # no state was issued for a request that was never built


def test_builder_refuses_a_request_that_would_miss_a_required_parameter(monkeypatch):
    import urllib.parse
    real = urllib.parse.urlencode
    monkeypatch.setattr(urllib.parse, "urlencode", lambda params, *a, **k: real({k_: v for k_, v in params.items() if k_ != "response_type"}, *a, **k))
    with pytest.raises(ValidationFailed, match="could not be built correctly"):
        svc.build_authorization_url(client_id=CLIENT_ID, redirect_uri=CODESPACES, scopes=["a"], state="s" * 32, code_challenge="c")
    monkeypatch.undo()
    with pytest.raises(ValidationFailed):
        svc.build_authorization_url(client_id="not-a-google-client", redirect_uri=CODESPACES, scopes=["a"], state="s", code_challenge="c")


def test_start_requires_super_admin_and_a_saved_client(monkeypatch):
    assert _start(ADMIN)[1].status_code == 403
    monkeypatch.delenv("GOOGLE_ADS_CLIENT_SECRET")
    reset_settings_cache()
    svc.invalidate()
    _, r = _start()
    assert r.status_code == 422 and "client ID and secret" in r.json()["error"]["message"]


def test_the_start_endpoint_never_logs_or_stores_the_state_challenge_or_secret(monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_REDIRECT_URI", CODESPACES)
    reset_settings_cache()
    api, r = _start()
    url = r.json()["authorization_url"]
    q = _q(url)
    with session_scope() as db:
        logs = json.dumps([[a.action, a.details, a.entity_id] for a in db.query(ActivityLog).filter(ActivityLog.action == "integrations.oauth_started")])
        row = db.query(OAuthState).one()
        assert q["state"] not in logs and q["code_challenge"] not in logs and SECRET not in logs and "accounts.google.com" not in logs
        assert row.state_hash != q["state"] and len(row.state_hash) == 64  # only a hash of the state is stored
        assert row.verifier_ciphertext and row.redirect_uri == CODESPACES  # the verifier is encrypted; the exact redirect URI is remembered for the exchange


# --------------------------------------------------------------------------- the callback: state validation and code exchange
def _google_token(captured):
    def handler(req):
        captured.append(dict(parse_qs(req.content.decode())))
        return httpx.Response(200, json={"refresh_token": "1//new-refresh-token-0123", "access_token": "ya29.x",
                                         "scope": "openid email https://www.googleapis.com/auth/adwords"})
    return handler


def _issue(who_id):
    with session_scope() as db:
        out = svc.oauth_start(db, ["google_ads"], who_id, origin="")
        return _q(out["authorization_url"])["state"], out["redirect_uri"]


def _user_id(email):
    from app.models import User
    with session_scope() as db:
        return db.query(User).filter_by(email=email).one().id


def test_code_exchange_uses_pkce_verifier_stored_redirect_uri_and_saves_the_refresh_token_encrypted(monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_REDIRECT_URI", CODESPACES)
    reset_settings_cache()
    uid = _user_id(SUPER)
    state, _ = _issue(uid)
    seen = []
    with session_scope() as db:
        out = svc.oauth_callback(db, state, "4/0AX-auth-code", uid, http=httpx.Client(transport=httpx.MockTransport(_google_token(seen))))
        assert out["saved"] == ["google_ads"]
        row = svc._row(db, "google_ads")
        assert "1//new-refresh-token" not in row.secrets_ciphertext and row.secret_fingerprints.get("refresh_token")
    form = {k: v[0] for k, v in seen[0].items()}
    assert form["grant_type"] == "authorization_code" and form["redirect_uri"] == CODESPACES and form["code"] == "4/0AX-auth-code"
    assert len(form["code_verifier"]) >= 43 and form["client_id"] == CLIENT_ID


def test_state_is_single_use_expires_and_is_bound_to_the_user(monkeypatch):
    uid, other = _user_id(SUPER), _user_id(ADMIN)
    client = httpx.Client(transport=httpx.MockTransport(_google_token([])))
    state, _ = _issue(uid)
    with session_scope() as db:  # another user cannot complete someone else's authorization
        with pytest.raises(ValidationFailed, match="Invalid or expired"):
            svc.oauth_callback(db, state, "c", other, http=client)
        db.rollback()
    state, _ = _issue(uid)
    with session_scope() as db:
        svc.oauth_callback(db, state, "c", uid, http=client)
    with session_scope() as db:  # replay
        with pytest.raises(ValidationFailed, match="Invalid or expired"):
            svc.oauth_callback(db, state, "c", uid, http=client)
        db.rollback()
    state, _ = _issue(uid)
    with session_scope() as db:  # expiry
        import hashlib
        db.query(OAuthState).filter_by(state_hash=hashlib.sha256(state.encode()).hexdigest()).update({"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
    with session_scope() as db:
        with pytest.raises(ValidationFailed, match="Invalid or expired"):
            svc.oauth_callback(db, state, "c", uid, http=client)
        db.rollback()
    with session_scope() as db:  # unknown / forged state
        with pytest.raises(ValidationFailed):
            svc.oauth_callback(db, "forged-state", "c", uid, http=client)
        db.rollback()


def test_callback_endpoint_redirects_with_a_reason_and_never_echoes_the_code_or_state():
    api = Api(SUPER)
    r = api.c.get("/api/v1/integrations/google/callback?state=forged&code=4/0SECRET-CODE", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"].startswith("/integrations?oauth=failed&reason=")
    assert "SECRET-CODE" not in r.headers["location"] and "forged" not in r.headers["location"]
    r = api.c.get("/api/v1/integrations/google/callback?error=access_denied%26x%3D1", follow_redirects=False)
    assert r.headers["location"] == "/integrations?oauth=failed&reason=access_denied%26x%3D1"  # the reason is URL-encoded, not injected into the query
    assert Api(ADMIN).c.get("/api/v1/integrations/google/callback?state=x&code=y", follow_redirects=False).status_code == 403
    assert Api().c.get("/api/v1/integrations/google/callback?state=x&code=y", follow_redirects=False).status_code == 401


def test_google_refusing_the_code_is_reported_without_leaking_it():
    uid = _user_id(SUPER)
    state, _ = _issue(uid)
    deny = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(400, json={"error": "invalid_grant", "error_description": "Bad code"})))
    with session_scope() as db:
        with pytest.raises(ValidationFailed, match="Google rejected the authorization code") as exc:
            svc.oauth_callback(db, state, "4/0LEAK-CODE", uid, http=deny)
        assert "4/0LEAK-CODE" not in exc.value.message
        db.rollback()


def test_oauth_work_does_not_touch_the_safety_defaults():
    from app.core.config import get_settings
    s = get_settings()
    assert s.GOOGLE_ADS_WRITES_ENABLED is False and s.AUTO_NEGATIVES_MODE == "shadow" and s.EMERGENCY_BRAKE_WRITE_MODE == "simulate"
