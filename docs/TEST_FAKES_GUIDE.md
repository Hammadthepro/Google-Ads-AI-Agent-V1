# Test fakes (tests only)

The application contains **no** mock data, mock adapters or simulated clock. Everything that used to be a simulated
world lives under `backend/tests/fakes/` and is imported only by tests:

| File | What it is |
|---|---|
| `tests/fakes/mock_world/` | deterministic Google Ads world (seed, simulator, failure injection) used to exercise business logic |
| `tests/fakes/google_ads.py`, `ai.py`, `places.py`, `pagespeed.py`, `crawler.py`, `keyword_research.py`, `notifications.py` | fake adapters |

Rules:
* `app/` never imports `tests` (a security test enforces it) and never defines `Mock*`/`Fake*`/`Fixture*` classes.
* `tests/conftest.py` installs the fakes with `registry.override(...)`. Tests marked `@pytest.mark.real_registry` run **without** them to
  exercise the real configuration gate (`IntegrationNotConfigured`).
* Fakes label their output `MOCK`; the UI shows that as "TEST FIXTURE", never as live data.
* The suite pins the clock to 18:30 UTC (the fake world derives spend-so-far from the time of day).
* Real-service checks are in `backend/live_tests` (`bash scripts/live-tests.sh`): PASSED / FAILED / NOT CONFIGURED / BLOCKED.
