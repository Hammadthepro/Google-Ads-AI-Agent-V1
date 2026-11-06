"""Test setup: a dedicated PostgreSQL test database, migrated with Alembic and seeded deterministically once per session.

TEST_DATABASE_URL (default: local google_ads_agent_test) must point at a disposable database.
"""
from __future__ import annotations

import os

os.environ.setdefault("TEST_DATABASE_URL", "postgresql+psycopg://app:app@localhost:5432/google_ads_agent_test")
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ.update({"APP_ENV": "test", "INTEGRATION_MODE": "mock", "TEST_MODE": "true", "GOOGLE_ADS_WRITES_ENABLED": "false",
                   "AI_PROVIDER": "mock", "AUTH_PROVIDER": "mock", "CRAWLER_MODE": "fixture", "CELERY_TASK_ALWAYS_EAGER": "true",
                   "GROQ_ENABLED": "false", "AUTO_NEGATIVES_MODE": "shadow",
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
    from app.mock_world import seed
    seed.reset(include_users=True)
    seed.seed()
    yield


@pytest.fixture(autouse=True)
def _clean_adapters():
    yield
    registry.clear_overrides()
    registry.reset()
    from app.db.session import session_scope
    from app.models import MockFailureInjection
    with session_scope() as db:
        db.query(MockFailureInjection).update({"is_active": False})


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
