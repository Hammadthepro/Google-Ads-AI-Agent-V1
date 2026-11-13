"""RealGoogleAdsAdapter — official google-ads Python library.

STATUS: the connection test (OAuth, accessible customers, customer read) is verified live; reading campaign data from a
non-test account needs a higher API access level than Test for the Google Cloud project that owns the OAuth client.
Contract tests run against a fake client; live tests report NOT CONFIGURED / BLOCKED instead of passing without credentials.

Defence in depth: `mutate()` refuses every non-validate-only call unless ALL write keys are set,
independently of the application's WriteGuard.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date
from typing import Any, Callable

from app.adapters.base import AdapterError, AdapterTimeout, AuthError, InvalidInput, PermissionDenied, \
    RateLimited, ServiceUnavailable, WritesDisabled
from app.adapters.google_ads.base import RESOURCE_COLLECTIONS, GoogleAdsAdapter
from app.core.config import get_settings

log = logging.getLogger("gaa.google_ads")
READ_ATTEMPTS = 4  # one try + up to three retries with exponential backoff (1s, 2s, 4s) on rate limits / transient outages

# App-only keys that are not Google Ads API fields
_APP_ONLY = {"type", "service", "scenario", "weak", "landing_page", "start_date", "status_history"}
_OP_TYPES = {
    "campaign_budget": ("CampaignBudget", "campaign_budget_operation"),
    "campaign": ("Campaign", "campaign_operation"),
    "campaign_criterion": ("CampaignCriterion", "campaign_criterion_operation"),
    "ad_group": ("AdGroup", "ad_group_operation"),
    "ad_group_criterion": ("AdGroupCriterion", "ad_group_criterion_operation"),
    "ad_group_ad": ("AdGroupAd", "ad_group_ad_operation"),
    "asset": ("Asset", "asset_operation"),
    "campaign_asset": ("CampaignAsset", "campaign_asset_operation"),
    "shared_set": ("SharedSet", "shared_set_operation"),
    "shared_criterion": ("SharedCriterion", "shared_criterion_operation"),
    "campaign_shared_set": ("CampaignSharedSet", "campaign_shared_set_operation"),
    "conversion_action": ("ConversionAction", "conversion_action_operation"),
}

_GAQL = {
    "campaign": ("SELECT campaign.resource_name, campaign.id, campaign.name, campaign.status, "
                 "campaign.advertising_channel_type, campaign.campaign_budget, campaign.bidding_strategy_type, "
                 "campaign.bidding_strategy_system_status FROM campaign WHERE campaign.status != 'REMOVED'"),
    "ad_group": ("SELECT ad_group.resource_name, ad_group.id, ad_group.name, ad_group.status, ad_group.campaign "
                 "FROM ad_group WHERE ad_group.status != 'REMOVED'"),
    "ad_group_ad": ("SELECT ad_group_ad.resource_name, ad_group_ad.ad.id, ad_group_ad.status, ad_group_ad.ad_group, "
                    "ad_group_ad.ad.final_urls, ad_group_ad.ad.responsive_search_ad.headlines, "
                    "ad_group_ad.ad.responsive_search_ad.descriptions FROM ad_group_ad "
                    "WHERE ad_group_ad.status != 'REMOVED'"),
    "ad_group_criterion": ("SELECT ad_group_criterion.resource_name, ad_group_criterion.criterion_id, "
                           "ad_group_criterion.status, ad_group_criterion.ad_group, ad_group_criterion.type, "
                           "ad_group_criterion.negative, ad_group_criterion.keyword.text, "
                           "ad_group_criterion.keyword.match_type FROM ad_group_criterion "
                           "WHERE ad_group_criterion.status != 'REMOVED'"),
    "campaign_criterion": ("SELECT campaign_criterion.resource_name, campaign_criterion.criterion_id, "
                           "campaign_criterion.campaign, campaign_criterion.type, campaign_criterion.negative, "
                           "campaign_criterion.keyword.text, campaign_criterion.keyword.match_type, "
                           "campaign_criterion.location.geo_target_constant FROM campaign_criterion"),
    "conversion_action": ("SELECT conversion_action.resource_name, conversion_action.id, conversion_action.name, "
                          "conversion_action.type, conversion_action.origin, conversion_action.category, "
                          "conversion_action.primary_for_goal, conversion_action.counting_type, "
                          "conversion_action.status, conversion_action.phone_call_duration_seconds, "
                          "conversion_action.value_settings.default_value, conversion_action.value_settings.always_use_default_value, "
                          "conversion_action.attribution_model_settings.attribution_model, conversion_action.click_through_lookback_window_days "
                          "FROM conversion_action WHERE conversion_action.status != 'REMOVED'"),
}


_CODE_RE = re.compile(r"(?:authorization_error|authorizationError|request_error|requestError|query_error|queryError|quota_error|quotaError)"
                      r"\W{1,6}([A-Z][A-Z_]{3,})")
_KNOWN_CODES = ("CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION", "USER_PERMISSION_DENIED", "CUSTOMER_NOT_ENABLED", "CUSTOMER_NOT_FOUND",
                "OAUTH_TOKEN_INVALID", "OAUTH_TOKEN_EXPIRED", "OAUTH_TOKEN_REVOKED", "INVALID_CUSTOMER_ID", "NOT_ADS_USER", "RESOURCE_EXHAUSTED",
                "RATE_EXCEEDED", "invalid_grant")


def error_code(text: str) -> str:
    """The Google Ads error code inside a failure message (safe to show/store: it is an enum name, not data)."""
    for k in _KNOWN_CODES:
        if k in text:
            return k
    m = _CODE_RE.search(text)
    return m.group(1) if m else ""


def real_writes_permitted() -> tuple[bool, str]:
    s = get_settings()
    if not s.GOOGLE_ADS_WRITES_ENABLED:
        return False, "GOOGLE_ADS_WRITES_ENABLED is false"
    from app.services import settings_service
    if not settings_service.get_value("real_writes_authorized", False):
        return False, "Super Admin has not authorized real writes (system setting real_writes_authorized)"
    from app.db.session import session_scope
    from app.services import integrations_service
    with session_scope() as db:
        if integrations_service.operating_mode(db) != "execution":
            return False, "The app is in read-only mode (Integrations › Operating mode)"
    return True, ""


class RealGoogleAdsAdapter(GoogleAdsAdapter):
    name = "RealGoogleAdsAdapter"
    source = "LIVE"
    is_real = True

    def __init__(self, client_factory: Callable[[], Any] | None = None, refresh_token: str | None = None):
        self._clients: dict[str, Any] = {}  # one Google client per login-customer-id (accounts can sit under different managers)
        self._factory = client_factory
        self._refresh_token = refresh_token
        self._sleep = time.sleep  # replaced in tests so backoff does not slow them down

    # ------------------------------------------------------------------ client
    def _login_for(self, customer_id: str) -> str:
        """The manager (login-customer-id) through which this account is reached: stored by account discovery, else the optional
        deployment default. Empty = the Google user has direct access."""
        from sqlalchemy import select

        from app.db.session import session_scope
        from app.models import GoogleAdsAccount
        cid = customer_id.replace("-", "")
        with session_scope() as db:
            login = db.scalar(select(GoogleAdsAccount.login_customer_id).where(GoogleAdsAccount.customer_id == cid))
        if login:
            return login
        from app.services import integrations_service
        return (integrations_service.resolve("google_ads").get("login_customer_id") or "").replace("-", "")

    def client(self, login: str | None = None):
        """Google client for a login-customer-id (None = the optional default)."""
        if login is None:
            from app.services import integrations_service
            login = (integrations_service.resolve("google_ads").get("login_customer_id") or "").replace("-", "") if not self._factory else ""
        key = login or ""
        if key not in self._clients:
            if self._factory:
                self._clients[key] = self._factory()
            else:
                from google.ads.googleads.client import GoogleAdsClient  # imported only here
                from app.services import integrations_service
                v = integrations_service.resolve("google_ads")
                token = self._refresh_token or v.get("refresh_token", "")
                missing = [k for k in ("client_id", "client_secret") if not v.get(k)]
                if missing or not token:
                    raise AuthError(f"Google Ads credentials missing: {missing + ([] if token else ['refresh_token'])}",
                                    adapter=self.name)
                # No developer token: Google sunset them on 2026-09-09. API access levels now belong to the Google Cloud project
                # that owns this OAuth client (https://developers.google.com/google-ads/api/docs/api-policy/developer-token).
                cfg = {"client_id": v["client_id"], "client_secret": v["client_secret"], "refresh_token": token, "use_proto_plus": False}
                if key:
                    cfg["login_customer_id"] = key
                self._clients[key] = GoogleAdsClient.load_from_dict(cfg)
        return self._clients[key]

    def _wrap(self, fn: Callable):
        try:
            return fn()
        except AdapterError:
            raise
        except Exception as exc:  # map vendor errors to adapter errors
            raise self._map_error(exc) from exc

    def _read(self, fn: Callable):
        """Reads are idempotent: back off and retry on rate limits and transient outages. Writes never use this (an
        AdapterTimeout on a write means the outcome is unknown and must be reconciled, not repeated)."""
        delay = 1.0
        for attempt in range(1, READ_ATTEMPTS + 1):
            try:
                return self._wrap(fn)
            except (RateLimited, ServiceUnavailable) as exc:
                if attempt == READ_ATTEMPTS:
                    raise
                log.warning("google ads read retry %s/%s after %s (sleeping %.0fs)", attempt, READ_ATTEMPTS, exc.kind, delay)
                self._sleep(delay)
                delay *= 2

    def _map_error(self, exc: Exception) -> AdapterError:
        err = self._map_error_inner(exc)
        err.details.setdefault("code", error_code(str(exc)))
        return err

    def _map_error_inner(self, exc: Exception) -> AdapterError:
        """Vendor errors become adapter errors with a SAFE message; the raw text goes to the backend log only."""
        text = str(exc)
        name = type(exc).__name__
        log.warning("google ads error %s: %s", name, text[:800])
        if "AUTHENTICATION" in text or "invalid_grant" in text or "UNAUTHENTICATED" in text:
            self._flag_integration("AUTHORIZATION_EXPIRED", "Google rejected the saved authorization. Reconnect Google Ads on the Integrations page.")
            return AuthError("Google Ads rejected the saved authorization. Reconnect Google Ads on the Integrations page.", adapter=self.name)
        if "CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION" in text:
            return PermissionDenied("Your Google Cloud project has Test access to the Google Ads API, which only works with test accounts, and this account is not a test "
                                    "account. Use a test account ID, or apply for a higher API access level to read real accounts.", adapter=self.name,
                                    details={"reason": "CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION"})
        if "DEVELOPER_TOKEN_NOT_APPROVED" in text or "explorer access" in text:
            return PermissionDenied("Google refused this method for the Cloud project's Explorer API access level. Keyword Planner and some other methods need "
                                    "Basic or Standard access: apply in the Google Ads API Center. The account and OAuth are not the problem.", adapter=self.name,
                                    details={"reason": "DEVELOPER_TOKEN_NOT_APPROVED", "requires_approval": True})
        if "AUTHORIZATION" in text or "PERMISSION_DENIED" in text or "USER_PERMISSION_DENIED" in text:
            return PermissionDenied("The authorised Google user cannot access this Google Ads account. Check the customer ID and manager (login) ID.", adapter=self.name)
        if "RESOURCE_EXHAUSTED" in text or "RATE_EXCEEDED" in text or "QuotaError" in text:
            return RateLimited("Google Ads rate limit reached. Retry in a few minutes.", adapter=self.name)
        if "DEADLINE_EXCEEDED" in text or "Timeout" in name:
            return AdapterTimeout("Google Ads did not answer in time.", adapter=self.name)
        if "UNAVAILABLE" in text:
            return ServiceUnavailable("Google Ads is temporarily unavailable.", adapter=self.name)
        return InvalidInput("Google Ads rejected the request.", adapter=self.name, details={"reason": text[:200]})

    def _flag_integration(self, status: str, message: str) -> None:
        """Make an auth failure that happens at runtime visible on the Integrations page (no secret is touched)."""
        try:
            from app.services import integrations_service
            integrations_service.record_runtime_failure("google_ads", status, message)
        except Exception:  # noqa: BLE001 - reporting must never mask the original error
            log.exception("could not record google_ads runtime failure")

    def _search(self, customer_id: str, query: str, login: str | None = None) -> list[Any]:
        if login is None and not self._factory:
            login = self._login_for(customer_id)

        def run():
            svc = self.client(login).get_service("GoogleAdsService")
            rows = []
            for batch in svc.search_stream(customer_id=customer_id.replace("-", ""), query=query):
                rows.extend(batch.results)
            return rows
        return self._read(run)

    # ------------------------------------------------------------------ accounts
    def list_accessible_customers(self) -> list[dict]:
        def run():
            svc = self.client().get_service("CustomerService")
            names = svc.list_accessible_customers().resource_names
            return [{"customer_id": n.split("/")[-1], "descriptive_name": "", "is_manager": None,
                     "manager_customer_id": "", "currency_code": "", "time_zone": ""} for n in names]
        return self._wrap(run)

    def get_customer(self, customer_id: str) -> dict:
        rows = self._search(customer_id, "SELECT customer.id, customer.descriptive_name, customer.currency_code, "
                                         "customer.time_zone, customer.manager, customer.auto_tagging_enabled FROM customer")
        if not rows:
            raise InvalidInput("Customer not found", adapter=self.name)
        c = rows[0].customer
        return {"customer_id": str(c.id), "descriptive_name": c.descriptive_name, "currency_code": c.currency_code,
                "time_zone": c.time_zone, "is_manager": c.manager, "auto_tagging_enabled": c.auto_tagging_enabled,
                "manager_customer_id": ""}

    def get_account_hierarchy(self, manager_customer_id: str) -> list[dict]:
        rows = self._search(manager_customer_id,
                            "SELECT customer_client.id, customer_client.descriptive_name, customer_client.manager, "
                            "customer_client.currency_code, customer_client.time_zone, customer_client.level "
                            "FROM customer_client WHERE customer_client.level <= 1")
        return [{"customer_id": str(r.customer_client.id), "descriptive_name": r.customer_client.descriptive_name,
                 "is_manager": r.customer_client.manager, "manager_customer_id": manager_customer_id,
                 "currency_code": r.customer_client.currency_code, "time_zone": r.customer_client.time_zone}
                for r in rows if str(r.customer_client.id) != manager_customer_id.replace("-", "")]

    # ------------------------------------------------------------------ manager discovery (read-only)
    @staticmethod
    def _plain(msg: Any) -> dict:
        """A protobuf message (or a plain object in unit tests) as {field: value} with enums as names and int64 as ints."""
        if hasattr(msg, "DESCRIPTOR"):
            from google.protobuf.json_format import MessageToDict
            return MessageToDict(msg, preserving_proto_field_name=True)
        return {k: getattr(msg, k) for k in dir(msg) if not k.startswith("_") and not callable(getattr(msg, k))}

    def list_manager_candidates(self) -> list[dict]:
        names = self.list_accessible_customers()
        out = []
        for n in names:
            cid = n["customer_id"]
            row = {"customer_id": cid, "descriptive_name": "", "is_manager": None, "status": "UNKNOWN", "test_account": False,
                   "currency_code": "", "time_zone": "", "error": ""}
            try:  # no login-customer-id: the user is accessing the account directly
                rows = self._search(cid, "SELECT customer.id, customer.descriptive_name, customer.manager, customer.test_account, "
                                         "customer.status, customer.currency_code, customer.time_zone FROM customer", login="")
            except AdapterError as exc:
                row["error"] = exc.details.get("code") or exc.kind
                out.append(row)
                continue
            c = self._plain(rows[0].customer) if rows else {}
            row.update({"descriptive_name": c.get("descriptive_name", ""), "is_manager": bool(c.get("manager")),
                        "status": c.get("status", "UNKNOWN"), "test_account": bool(c.get("test_account")),
                        "currency_code": c.get("currency_code", ""), "time_zone": c.get("time_zone", "")})
            out.append(row)
        return out

    _CLIENTS_Q = ("SELECT customer_client.id, customer_client.descriptive_name, customer_client.status, customer_client.manager, "
                  "customer_client.level, customer_client.currency_code, customer_client.time_zone, customer_client.test_account, "
                  "customer_client.hidden FROM customer_client WHERE customer_client.level <= 1")
    _SKIP_DESCENT = {"CLOSED", "CANCELED"}
    MAX_MANAGERS = 500  # safety bound on the sub-manager walk

    def list_customer_clients(self, manager_customer_id: str) -> dict:
        """Walk the hierarchy below `manager_customer_id` one manager at a time (level <= 1 per manager) so every account's PARENT
        is known. `search_stream` delivers all result batches, which is how pagination is covered. A sub-manager that cannot be
        read is reported in `errors` and its subtree is left out — never guessed."""
        root = manager_customer_id.replace("-", "")
        accounts: dict[str, dict] = {}
        errors: list[dict] = []
        queue, seen = [(root, "", 0)], set()
        while queue and len(seen) < self.MAX_MANAGERS:
            mgr, parent_of_mgr, lvl = queue.pop(0)
            if mgr in seen:
                continue
            seen.add(mgr)
            try:
                rows = self._search(mgr, self._CLIENTS_Q, login=root)
            except AuthError:
                raise  # invalid/expired credentials affect every branch: stop instead of reporting a hundred identical branch errors
            except AdapterError as exc:
                errors.append({"customer_id": mgr, "code": exc.details.get("code") or exc.kind})
                if mgr in accounts:
                    accounts[mgr]["error"] = exc.details.get("code") or exc.kind
                continue
            for r in rows:
                c = self._plain(r.customer_client)
                cid = str(c.get("id", ""))
                if not cid:
                    continue
                item = {"customer_id": cid, "descriptive_name": c.get("descriptive_name", ""), "status": c.get("status", "UNKNOWN"),
                        "is_manager": bool(c.get("manager")), "currency_code": c.get("currency_code", ""), "time_zone": c.get("time_zone", ""),
                        "test_account": bool(c.get("test_account")), "hidden": bool(c.get("hidden")), "error": ""}
                if cid == mgr:  # level 0 row = the manager itself
                    item.update({"level": lvl, "parent_customer_id": parent_of_mgr})
                    accounts[cid] = {**accounts.get(cid, {}), **item}
                    continue
                item.update({"level": lvl + 1, "parent_customer_id": mgr})
                accounts.setdefault(cid, item)
                if item["is_manager"] and item["status"] not in self._SKIP_DESCENT and cid not in seen:
                    queue.append((cid, mgr, lvl + 1))
        return {"accounts": list(accounts.values()), "errors": errors}

    # ------------------------------------------------------------------ structure
    def list_resources(self, customer_id: str, resource_type: str, parent: str | None = None,
                       label: str | None = None) -> list[dict]:
        query = _GAQL.get(resource_type)
        if not query:
            return []
        from google.protobuf.json_format import MessageToDict
        out = []
        for row in self._search(customer_id, query):
            msg = getattr(row, resource_type)
            d = MessageToDict(msg, preserving_proto_field_name=True)
            d["resource_type"] = resource_type
            d["id"] = str(d.get("id") or d.get("criterion_id") or d.get("ad", {}).get("id", ""))
            d["parent"] = d.get("campaign") or d.get("ad_group") or ""
            if resource_type == "ad_group_ad":
                rsa = d.get("ad", {}).get("responsive_search_ad", {})
                rsa["headlines"] = [h.get("text") for h in rsa.get("headlines", [])]
                rsa["descriptions"] = [h.get("text") for h in rsa.get("descriptions", [])]
            if parent and d["parent"] != parent:
                continue
            out.append(d)
        return out

    def get_resource(self, customer_id: str, resource_name: str) -> dict | None:
        collection = resource_name.split("/")[2]
        rtype = next((k for k, v in RESOURCE_COLLECTIONS.items() if v == collection), None)
        for r in self.list_resources(customer_id, rtype or ""):
            if r.get("resource_name") == resource_name:
                return r
        return None

    # ------------------------------------------------------------------ reporting
    def get_metrics(self, customer_id: str, start: date, end: date, level: str = "CAMPAIGN") -> list[dict]:
        entity = {"CAMPAIGN": "campaign", "AD_GROUP": "ad_group", "KEYWORD": "keyword_view", "AD": "ad_group_ad"}[level]
        id_field = {"CAMPAIGN": "campaign.id", "AD_GROUP": "ad_group.id", "KEYWORD": "ad_group_criterion.criterion_id",
                    "AD": "ad_group_ad.ad.id"}[level]
        is_fields = (", metrics.search_impression_share, metrics.search_budget_lost_impression_share, "
                     "metrics.search_rank_lost_impression_share") if level == "CAMPAIGN" else ""
        q = (f"SELECT segments.date, campaign.id, {id_field}, metrics.impressions, metrics.clicks, "
             f"metrics.cost_micros, metrics.conversions{is_fields} FROM {entity} "
             f"WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'")
        out = []
        for r in self._search(customer_id, q):
            ent = {"CAMPAIGN": lambda: r.campaign.id, "AD_GROUP": lambda: r.ad_group.id,
                   "KEYWORD": lambda: r.ad_group_criterion.criterion_id, "AD": lambda: r.ad_group_ad.ad.id}[level]()
            m = r.metrics
            out.append({"date": r.segments.date, "level": level, "entity_id": str(ent), "campaign_id": str(r.campaign.id),
                        "impressions": m.impressions, "clicks": m.clicks, "cost_micros": m.cost_micros,
                        "conversions": m.conversions, "form_conversions": None, "call_conversions": None,
                        "search_impression_share": getattr(m, "search_impression_share", None) if is_fields else None,
                        "search_budget_lost_impression_share": getattr(m, "search_budget_lost_impression_share", None) if is_fields else None,
                        "search_rank_lost_impression_share": getattr(m, "search_rank_lost_impression_share", None) if is_fields else None})
        return out

    def get_search_terms(self, customer_id: str, start: date, end: date) -> list[dict]:
        q = ("SELECT search_term_view.search_term, campaign.id, ad_group.id, metrics.impressions, metrics.clicks, "
             "metrics.cost_micros, metrics.conversions FROM search_term_view "
             f"WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'")
        return [{"campaign_id": str(r.campaign.id), "ad_group_id": str(r.ad_group.id),
                 "term": r.search_term_view.search_term, "matched_keyword": "", "impressions": r.metrics.impressions,
                 "clicks": r.metrics.clicks, "cost_micros": r.metrics.cost_micros, "conversions": r.metrics.conversions,
                 "last_conversion_date": None} for r in self._search(customer_id, q)]

    def get_call_records(self, customer_id: str, start: date, end: date) -> list[dict]:
        q = ("SELECT call_view.resource_name, call_view.start_call_date_time, call_view.call_duration_seconds, "
             "call_view.call_status, campaign.id FROM call_view "
             f"WHERE call_view.start_call_date_time >= '{start.isoformat()} 00:00:00' "
             f"AND call_view.start_call_date_time <= '{end.isoformat()} 23:59:59'")
        return [{"id": r.call_view.resource_name, "campaign_id": str(r.campaign.id),
                 "start_call_date_time": r.call_view.start_call_date_time,
                 "call_duration_seconds": r.call_view.call_duration_seconds,
                 "call_status": "ANSWERED" if r.call_view.call_status.name == "RECEIVED" else "MISSED"}
                for r in self._search(customer_id, q)]

    def get_change_events(self, customer_id: str, since_iso: str) -> list[dict]:
        q = ("SELECT change_event.resource_name, change_event.change_date_time, change_event.change_resource_name, "
             "change_event.user_email, change_event.client_type, change_event.resource_change_operation "
             f"FROM change_event WHERE change_event.change_date_time >= '{since_iso[:19].replace('T', ' ')}' "
             "ORDER BY change_event.change_date_time LIMIT 1000")
        return [{"id": r.change_event.resource_name, "change_resource_name": r.change_event.change_resource_name,
                 "resource_change_operation": r.change_event.resource_change_operation.name,
                 "user_email": r.change_event.user_email, "client_type": r.change_event.client_type.name,
                 "change_date_time": r.change_event.change_date_time, "details": {}}
                for r in self._search(customer_id, q)]

    def get_recommendations(self, customer_id: str) -> list[dict]:
        q = "SELECT recommendation.resource_name, recommendation.type, recommendation.campaign FROM recommendation"
        return [{"type": r.recommendation.type_.name if hasattr(r.recommendation, "type_") else str(r.recommendation.type),
                 "campaign_id": r.recommendation.campaign.split("/")[-1], "detail": ""}
                for r in self._search(customer_id, q)]

    def get_today_spend(self, customer_id: str) -> list[dict]:
        q = ("SELECT campaign.resource_name, campaign.id, campaign.name, campaign_budget.amount_micros, "
             "metrics.cost_micros FROM campaign WHERE segments.date DURING TODAY AND campaign.status = 'ENABLED'")
        return [{"campaign_resource": r.campaign.resource_name, "campaign_id": str(r.campaign.id),
                 "name": r.campaign.name, "cost_micros": r.metrics.cost_micros,
                 "daily_budget_micros": r.campaign_budget.amount_micros, "as_of": ""} for r in self._search(customer_id, q)]

    def get_experiment_results(self, customer_id: str, experiment_resource: str) -> dict:
        raise InvalidInput("Experiment results via ExperimentService are not implemented for the real adapter yet "
                           "(see docs/KNOWN_ISSUES.md)", adapter=self.name)

    # ------------------------------------------------------------------ writes
    def build_operations(self, customer_id: str, operations: list[dict]) -> list[Any]:
        """Translate application operation dicts into GoogleAdsService MutateOperation protos."""
        from google.protobuf.json_format import ParseDict
        client = self.client(None if self._factory else self._login_for(customer_id))
        cid = customer_id.replace("-", "")
        temp_map: dict[str, str] = {}
        for op in operations:
            if op.get("op") == "create" and op.get("temp_id"):
                temp_map[str(op["temp_id"])] = f"customers/{cid}/{RESOURCE_COLLECTIONS[op['resource']]}/{op['temp_id']}"

        def resolve(v):
            if isinstance(v, str) and v.startswith("temp:"):
                return temp_map[v[5:]]
            if isinstance(v, dict):
                return {k: resolve(x) for k, x in v.items()}
            if isinstance(v, list):
                return [resolve(x) for x in v]
            return v

        protos = []
        for op in operations:
            rtype = op["resource"]
            if rtype not in _OP_TYPES:
                raise InvalidInput(f"Resource {rtype} is not supported by the real adapter", adapter=self.name)
            if op["op"] not in ("create", "update"):
                raise InvalidInput("Only create/update operations are permitted", adapter=self.name)
            fields = {k: v for k, v in resolve(op.get("fields") or {}).items() if k not in _APP_ONLY}
            if rtype == "ad_group_ad":
                rsa = (fields.get("ad") or {}).get("responsive_search_ad")
                if rsa:
                    rsa["headlines"] = [{"text": h} for h in rsa.get("headlines", [])]
                    rsa["descriptions"] = [{"text": d} for d in rsa.get("descriptions", [])]
            msg_name, attr = _OP_TYPES[rtype]
            mutate_op = client.get_type("MutateOperation")
            sub = getattr(mutate_op, attr)
            if op["op"] == "create":
                if op.get("temp_id"):
                    fields["resource_name"] = temp_map[str(op["temp_id"])]
                ParseDict(fields, sub.create, ignore_unknown_fields=True)
            else:
                fields["resource_name"] = op["resource_name"]
                ParseDict(fields, sub.update, ignore_unknown_fields=True)
                paths = [k for k in fields if k != "resource_name"]
                sub.update_mask.paths.extend(paths)
            protos.append(mutate_op)
        return protos

    def mutate(self, customer_id: str, operations: list[dict], *, validate_only: bool = False,
               request_id: str = "") -> dict:
        if not validate_only:
            ok, why = real_writes_permitted()
            if not ok:
                raise WritesDisabled(f"Real Google Ads write blocked inside adapter: {why}", adapter=self.name)

        def run():
            client = self.client(None if self._factory else self._login_for(customer_id))
            svc = client.get_service("GoogleAdsService")
            request = client.get_type("MutateGoogleAdsRequest")
            request.customer_id = customer_id.replace("-", "")
            request.mutate_operations.extend(self.build_operations(customer_id, operations))
            request.validate_only = validate_only
            request.partial_failure = False  # atomic
            response = svc.mutate(request=request)
            results = []
            for op, res in zip(operations, response.mutate_operation_responses):
                field = res.WhichOneof("response")
                rn = getattr(res, field).resource_name if field else ""
                results.append({"op": op["op"], "resource": op["resource"], "resource_name": rn,
                                "temp_id": op.get("temp_id")})
            return {"validate_only": validate_only, "results": results, "source": "LIVE", "request_id": request_id}
        return self._wrap(run)
