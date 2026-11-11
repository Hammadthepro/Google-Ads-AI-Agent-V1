"""The Google Business Profile API integration is gone; the profile LINK in client intake is the only remaining input."""
from __future__ import annotations

import pathlib

from app.services import integrations_service as svc
from tests.conftest import SUPER, Api

APP = pathlib.Path(svc.__file__).resolve().parents[1]


def test_no_gbp_provider_card_status_or_dependency():
    sup = Api(SUPER)
    assert "gbp" not in svc.PROVIDERS and "gbp" not in svc.GOOGLE_SCOPES
    listing = sup.get("/integrations").json()["integrations"]
    assert all("business" not in (i["provider"] + i["label"]).lower() for i in listing)
    health = sup.get("/integrations/health").json()["items"]
    assert "gbp" not in {i["provider"] for i in health}
    for item in listing:  # no other integration depends on it
        assert "gbp" not in str(item.get("dependencies", "")).lower()
    assert sup.get("/integrations/gbp").status_code in (404, 422)
    assert sup.get("/integrations/gbp/options").status_code in (404, 422)
    assert sup.post("/integrations/gbp/test").status_code in (404, 422)


def test_no_application_code_calls_the_business_profile_apis_or_asks_for_their_scope():
    banned = ("business.manage", "mybusinessaccountmanagement", "mybusinessbusinessinformation", "mybusiness.googleapis")
    hits = [f"{p.relative_to(APP)}: {b}" for p in APP.rglob("*.py") for b in banned if b in p.read_text()]
    assert not hits, hits


def test_a_stale_saved_gbp_row_is_ignored_not_listed():
    from app.db.session import session_scope
    from app.models import IntegrationConfig
    with session_scope() as db:
        db.add(IntegrationConfig(provider="gbp", workspace_id=svc.workspace_id(db), settings={}, secret_fingerprints={}, last_test_steps=[], scopes=[], discovered={}))
    try:
        assert "gbp" not in {i["provider"] for i in Api(SUPER).get("/integrations").json()["integrations"]}
    finally:
        with session_scope() as db:
            db.query(IntegrationConfig).filter_by(provider="gbp").delete()
