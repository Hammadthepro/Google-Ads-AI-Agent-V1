# Real-integration-only operation

**Principle:** the app never shows fabricated information as real. Missing integrations produce a clear message, not data.

## Behaviour
| Situation | What happens |
|---|---|
| Integration not configured / disabled | `IntegrationNotConfigured` → HTTP 424, code `integration_not_configured`, message "<Name> integration is not configured. Please configure and test your connection on the Integrations page." No other provider is substituted. |
| Google Ads API fails | Safe message to the user (HTTP 424/429/503…), technical detail only in the backend log. Reads retry with backoff (1s, 2s, 4s) on rate limits/outages. Auth failure marks the integration "Connection failed" on the Integrations page. |
| Refresh fails after earlier success | Previously retrieved snapshots are kept untouched; screens show **Historical data** with the original retrieval time. |
| AI provider missing | AI steps report it; audits continue with the checks that do not need AI, and the report lists the rest as unavailable. |
| SMTP missing | Alerts stay in the app, recorded as `NOT_EMAILED`. Nothing is emailed without SMTP. |

## Data status shown in the UI
`not_configured` · `no_account` · `no_data` ("Connected but no data") · `data_available` · `historical` · `failed`
(`monitoring.data_status`, returned by `/dashboard` and `/monitor/{client}`).

## Operating modes
`read_only` (default, real account, no writes) and `execution` (validated writes). Writes additionally need
`GOOGLE_ADS_WRITES_ENABLED=true` **and** a Super Admin authorization; campaigns are created PAUSED; enabling needs its own approval;
`EMERGENCY_BRAKE_WRITE_MODE=simulate` records brake actions without executing them (a safety mode).

## Removing leftover demo data
`python -m app.cli purge-demo-data` (dry run) / `--apply`. Matches only simulated customers, `.example`-site or "(SIMULATED)" clients
(with everything hanging off them) and unlinked simulated accounts; demo users are deactivated, never deleted; activity logs and
integration credentials are kept.

## Not implemented
Lead Generation and Outreach agents, Market Research as a separate agent, and a GA4 analytics view do not exist in the codebase; none were
faked. See KNOWN_ISSUES.md.
