"""GoogleAdsAdapter interface.

Shapes mirror the Google Ads API so the real adapter is a mechanical mapping:
- resource names: customers/{cid}/{collection}/{id}
- money in micros, statuses ENABLED / PAUSED / REMOVED
- mutate = one atomic batch of operations with temporary ids (negative strings) for cross-references

A mutate operation is a dict:
    {"resource": "campaign_budget" | "campaign" | "campaign_criterion" | "ad_group" | "ad_group_criterion"
                 | "ad_group_ad" | "asset" | "campaign_asset" | "shared_set" | "shared_criterion"
                 | "campaign_shared_set" | "conversion_action" | "experiment",
     "op": "create" | "update",
     "temp_id": "-1",                       # creates only
     "resource_name": "customers/..",       # updates only
     "fields": {...}}
Field values may reference earlier creates as "temp:-1".
"remove" is intentionally not part of the contract used by the application (deletes are never automatic).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date


RESOURCE_COLLECTIONS = {
    "campaign_budget": "campaignBudgets",
    "campaign": "campaigns",
    "campaign_criterion": "campaignCriteria",
    "ad_group": "adGroups",
    "ad_group_criterion": "adGroupCriteria",
    "ad_group_ad": "adGroupAds",
    "asset": "assets",
    "campaign_asset": "campaignAssets",
    "shared_set": "sharedSets",
    "shared_criterion": "sharedCriteria",
    "campaign_shared_set": "campaignSharedSets",
    "conversion_action": "conversionActions",
    "experiment": "experiments",
}


class GoogleAdsAdapter(ABC):
    name = "google_ads"
    source = "NONE"
    is_real = False

    # ---- accounts
    @abstractmethod
    def list_accessible_customers(self) -> list[dict]: ...

    @abstractmethod
    def get_customer(self, customer_id: str) -> dict: ...

    @abstractmethod
    def get_account_hierarchy(self, manager_customer_id: str) -> list[dict]: ...

    # ---- structure
    @abstractmethod
    def list_resources(self, customer_id: str, resource_type: str, parent: str | None = None,
                       label: str | None = None) -> list[dict]: ...

    @abstractmethod
    def get_resource(self, customer_id: str, resource_name: str) -> dict | None: ...

    def list_campaigns(self, customer_id: str) -> list[dict]:
        return self.list_resources(customer_id, "campaign")

    def list_ad_groups(self, customer_id: str, campaign_resource: str | None = None) -> list[dict]:
        return self.list_resources(customer_id, "ad_group", parent=campaign_resource)

    def list_ads(self, customer_id: str, ad_group_resource: str | None = None) -> list[dict]:
        return self.list_resources(customer_id, "ad_group_ad", parent=ad_group_resource)

    def list_keywords(self, customer_id: str, ad_group_resource: str | None = None) -> list[dict]:
        return [r for r in self.list_resources(customer_id, "ad_group_criterion", parent=ad_group_resource)
                if r.get("type") == "KEYWORD"]

    def list_campaign_criteria(self, customer_id: str, campaign_resource: str | None = None) -> list[dict]:
        return self.list_resources(customer_id, "campaign_criterion", parent=campaign_resource)

    def list_conversion_actions(self, customer_id: str) -> list[dict]:
        return self.list_resources(customer_id, "conversion_action")

    # ---- reporting
    @abstractmethod
    def get_metrics(self, customer_id: str, start: date, end: date, level: str = "CAMPAIGN") -> list[dict]: ...

    @abstractmethod
    def get_search_terms(self, customer_id: str, start: date, end: date) -> list[dict]: ...

    @abstractmethod
    def get_call_records(self, customer_id: str, start: date, end: date) -> list[dict]: ...

    @abstractmethod
    def get_change_events(self, customer_id: str, since_iso: str) -> list[dict]: ...

    @abstractmethod
    def get_recommendations(self, customer_id: str) -> list[dict]: ...

    @abstractmethod
    def get_today_spend(self, customer_id: str) -> list[dict]: ...

    @abstractmethod
    def get_experiment_results(self, customer_id: str, experiment_resource: str) -> dict: ...

    # ---- writes
    @abstractmethod
    def mutate(self, customer_id: str, operations: list[dict], *, validate_only: bool = False,
               request_id: str = "") -> dict: ...
