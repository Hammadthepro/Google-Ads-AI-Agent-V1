"""Test setup: a dedicated PostgreSQL test database, migrated with Alembic and seeded deterministically once per session.

TEST_DATABASE_URL (default: local google_ads_agent_test) must point at a disposable database.
"""
from __future__ import annotations

import os

# Hermetic: real credentials in the developer's environment (cloud env vars, shell) must never reach the tests,
# otherwise "not configured" cases would call real Google/Groq/PageSpeed endpoints.
for _k in list(os.environ):
    if _k.startswith(("GOOGLE_", "GROQ_", "SMTP_", "ANTHROPIC_")) or _k in ("PAGESPEED_API_KEY", "BOOTSTRAP_ADMIN_EMAIL", "ALLOWED_LOGIN_DOMAINS", "APP_SECRET_KEY",
                                                                             "COMPOSE_DATABASE_URL"):
        os.environ.pop(_k)

os.environ.setdefault("TEST_DATABASE_URL", "postgresql+psycopg://app:app@localhost:5432/google_ads_agent_test")
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ.update({"GAA_ENV_FILE": "none", "TOKEN_ENCRYPTION_KEY": "9ZKj5vNq0y3uW8cFhB2tXsL1mP6dRaQe4oYiTnVgHwE=", "APP_ENV": "test", "GOOGLE_ADS_WRITES_ENABLED": "false",
                   "AUTH_PROVIDER": "dev", "CELERY_TASK_ALWAYS_EAGER": "true",
                   "AUTO_NEGATIVES_MODE": "shadow",
                   "RATE_LIMIT_PER_MINUTE": "100000", "AUTH_RATE_LIMIT_PER_MINUTE": "100000"})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.adapters import registry  # noqa: E402
from app.core.config import reset_settings_cache  # noqa: E402

reset_settings_cache()


def _migrate():
    from alembic import command
    from alembic.config import Config
    here = os.path.dirname(os.path.dirname(__file__))
    cfg = Config(os.path.join(here, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(here, "migrations"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
def seeded_db():
    from app.db.session import configure_engine
    configure_engine(os.environ["DATABASE_URL"])
    _migrate()
    # The fake Google Ads world derives "spend so far today" from the time of day, so pin the clock to 18:30 UTC of the real date:
    # otherwise rules such as the spend-spike brake only fire when the suite happens to run late in the day.
    from app.core import clock
    real_now = clock.now
    clock.now = lambda: real_now().replace(hour=18, minute=30, second=0, microsecond=0)
    from tests.fakes.mock_world import seed
    seed.reset(include_users=True)
    install_fakes()
    try:
        seed.seed()
    finally:
        registry.clear_overrides()
        registry.reset()
    yield
    clock.now = real_now


def install_fakes() -> None:
    """Isolated test doubles. They live under tests/fakes and are never imported by the application: the registry only
    ever builds real adapters, so any test that wants a simulated Google Ads/AI/etc. must opt in explicitly like this."""
    from tests.fakes.ai import MockAIProvider
    from tests.fakes.crawler import FixtureCrawlerAdapter
    from tests.fakes.google_ads import MockGoogleAdsAdapter
    from tests.fakes.keyword_research import MockKeywordResearchAdapter
    from tests.fakes.notifications import MockNotifier
    from tests.fakes.pagespeed import MockPageSpeedAdapter
    from tests.fakes.places import MockGooglePlacesAdapter
    registry.override("google_ads", MockGoogleAdsAdapter())
    registry.override("keyword_research", MockKeywordResearchAdapter())
    registry.override("places", MockGooglePlacesAdapter())
    registry.override("pagespeed", MockPageSpeedAdapter())
    registry.override("crawler", FixtureCrawlerAdapter())
    registry.override("ai", MockAIProvider())
    registry.override("challenger_ai", MockAIProvider())
    registry.override("notifier", MockNotifier())
    from tests.fakes.research import FakeOSM, FakeSuggestions
    registry.override("search_suggestions", FakeSuggestions())
    registry.override("openstreetmap", FakeOSM())


@pytest.fixture(scope="session", autouse=True)
def _fakes_installed(seeded_db):
    """Fakes stay installed for the whole session (module-scoped fixtures run before function-scoped ones)."""
    install_fakes()
    yield
    registry.clear_overrides()
    registry.reset()


@pytest.fixture(autouse=True)
def _adapters(request):
    """Tests marked `real_registry` run WITHOUT fakes so the real configuration gate is exercised; every test ends with a
    clean slate (fakes reinstalled, failure injections cleared)."""
    registry.reset()
    if "real_registry" in request.keywords:
        registry.clear_overrides()
        registry.reset()
    yield
    install_fakes()
    registry.reset()
    from app.db.session import session_scope
    from app.models import MockFailureInjection
    with session_scope() as db:
        db.query(MockFailureInjection).update({"is_active": False})


class SimClock:
    """Test-only time travel for the fake Google Ads world: shifts app.core.clock.now and runs the daily cycle per day."""

    def __init__(self, monkeypatch):
        from datetime import timedelta

        from app.core import clock
        self._real_now, self._td, self.hours = clock.now, timedelta, 0.0
        monkeypatch.setattr(clock, "now", lambda: self._real_now() + self._td(hours=self.hours))

    def advance_days(self, days: int) -> list:
        from app.workers import jobs
        out = []
        for _ in range(days):
            self.hours += 24
            out.append(jobs.run_daily_cycle())
        return out


@pytest.fixture
def sim_clock(monkeypatch):
    return SimClock(monkeypatch)


class Api:
    """TestClient wrapper that logs in through the real dev-login endpoint and sends the CSRF header."""

    def __init__(self, email: str | None = None):
        from app.main import app
        self.c = TestClient(app)
        self.csrf = ""
        if email:
            r = self.c.post("/api/v1/auth/dev-login", json={"email": email})
            assert r.status_code == 200, r.text
            self.csrf = r.json()["csrf_token"]

    def _h(self):
        return {"X-CSRF-Token": self.csrf} if self.csrf else {}

    def get(self, path, **kw):
        return self.c.get("/api/v1" + path, **kw)

    def post(self, path, json=None, **kw):
        return self.c.post("/api/v1" + path, json=json or {}, headers=self._h(), **kw)

    def patch(self, path, json=None):
        return self.c.patch("/api/v1" + path, json=json or {}, headers=self._h())

    def put(self, path, json=None):
        return self.c.put("/api/v1" + path, json=json or {}, headers=self._h())

    def delete(self, path):
        return self.c.delete("/api/v1" + path, headers=self._h())


SUPER, ADMIN, BACKUP, MANAGER, ANALYST = ("superadmin@napollo.example", "admin@napollo.example", "backup.admin@napollo.example",
                                          "manager@napollo.example", "analyst@napollo.example")


@pytest.fixture
def api_super():
    return Api(SUPER)


@pytest.fixture
def api_admin():
    return Api(ADMIN)


@pytest.fixture
def api_manager():
    return Api(MANAGER)


@pytest.fixture
def api_analyst():
    return Api(ANALYST)


@pytest.fixture
def db():
    from app.db.session import SessionLocal
    s = SessionLocal()
    yield s
    s.close()


def inject(adapter: str, failure: str, method: str = "*", entity: str = "", times: int = 1):
    from app.db.session import session_scope
    from app.models import MockFailureInjection
    with session_scope() as s:
        s.add(MockFailureInjection(adapter=adapter, method=method, failure=failure, entity=entity, times=times))
