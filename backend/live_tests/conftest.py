"""LIVE integration tests: they talk to the real external services with the credentials saved on the Integrations page
(or in the environment). They run against the configured DATABASE_URL and only READ — nothing here writes to Google Ads.

    cd backend && .venv/bin/python -m pytest live_tests -p no:cacheprovider -rs

Every test ends in exactly one of:  PASSED · FAILED · NOT CONFIGURED (credentials missing) · BLOCKED (configured, but an
external condition such as missing Google Ads API access prevents the check). NOT CONFIGURED and BLOCKED are reported as
skipped and listed in the summary — they are never counted as passed.
"""
from __future__ import annotations

import pytest

_results: list[tuple[str, str, str]] = []


def not_configured(provider: str, detail: str = "") -> None:
    pytest.skip(f"NOT CONFIGURED: {provider} {detail}".strip())


def blocked(reason: str) -> None:
    pytest.skip(f"BLOCKED: {reason}")


@pytest.fixture(scope="session")
def db_available():
    from sqlalchemy import text

    from app.db.session import session_scope
    try:
        with session_scope() as db:
            db.execute(text("select 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"BLOCKED: database unreachable ({type(exc).__name__})")


@pytest.fixture
def integration(db_available):
    """Run the real connection test for a provider; skips as NOT CONFIGURED when its required values are missing."""
    from app.db.session import session_scope
    from app.services import integrations_service as svc

    def run(provider: str, target: str = "all") -> dict:
        with session_scope() as db:
            row = svc._row(db, provider)
            miss = svc.missing(provider, row)
            if miss:
                not_configured(svc.PROVIDERS[provider]["label"], f"(missing: {', '.join(miss)})")
            res = svc.test_connection(db, provider, target=target)
        return res
    return run


def step_status(res: dict, name_part: str) -> str | None:
    return next((s["status"] for s in res["steps"] if name_part.lower() in s["name"].lower()), None)


def blocker(res: dict) -> str | None:
    """A failed step whose cause is outside the app (API access level, quota) rather than a bug."""
    text = " ".join(f"{s['detail']} {s.get('hint', '')}" for s in res["steps"] if s["status"] == "FAILED")
    for marker, why in (("CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION", "the Google Cloud project has Test access (test accounts only) and the configured customer is not a test account"),
                        ("No client account has been discovered yet", "no client account has been discovered (no enabled manager/MCC is reachable, so there is nothing to discover)"),
                        ("rate limit", "the provider rate limit is exhausted on this plan"),
                        ("DNS resolution failed", "this machine cannot resolve DNS itself (proxy-only network); the SSRF-guarded crawler needs normal DNS")):
        if marker.lower() in text.lower():
            return why
    return None


def pytest_runtest_logreport(report):
    if report.when == "call" or (report.when == "setup" and report.skipped):
        if report.skipped:
            reason = str(report.longrepr[-1]) if isinstance(report.longrepr, tuple) else str(report.longrepr)
            label = "BLOCKED" if "BLOCKED" in reason else "NOT CONFIGURED" if "NOT CONFIGURED" in reason else "SKIPPED"
            _results.append((report.nodeid.split("::")[-1], label, reason.replace("Skipped: ", "")))
        else:
            _results.append((report.nodeid.split("::")[-1], "PASSED" if report.passed else "FAILED", ""))


def pytest_terminal_summary(terminalreporter):
    tr = terminalreporter
    tr.section("LIVE INTEGRATION RESULTS (skipped is NOT passed)")
    for name, label, why in _results:
        tr.write_line(f"{label:<15} {name}  {why}")
    counts: dict[str, int] = {}
    for _, label, _ in _results:
        counts[label] = counts.get(label, 0) + 1
    tr.write_line("TOTAL: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
