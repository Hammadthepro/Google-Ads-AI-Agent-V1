"""Strict schemas for every AI output. Anything that does not validate is rejected and never executed."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServiceItem(Strict):
    name: str
    page_url: str = ""


class ServiceExtraction(Strict):
    services: list[ServiceItem] = Field(default_factory=list)
    service_areas: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)
    brand_words: list[str] = Field(default_factory=list)


class KeywordLabel(Strict):
    keyword: str
    label: Literal["SERVICE", "GENERIC", "IRRELEVANT"]
    service: str | None = None
    reason: str = ""


class KeywordLabels(Strict):
    labels: list[KeywordLabel]


class CompetitorAdsExtraction(Strict):
    offers: list[str] = Field(default_factory=list)
    angles: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)


class AdCopy(Strict):
    headlines: list[str] = Field(min_length=3, max_length=15)
    descriptions: list[str] = Field(min_length=2, max_length=4)
    path1: str = ""
    path2: str = ""


class KeywordDraft(Strict):
    text: str
    match_type: Literal["EXACT", "PHRASE", "BROAD"]


class AdGroupDraft(Strict):
    name: str
    service: str
    landing_page: str
    keywords: list[KeywordDraft] = Field(default_factory=list)
    negatives: list[str] = Field(default_factory=list)
    ads: list[AdCopy] = Field(default_factory=list, max_length=3)


class BudgetShare(Strict):
    ad_group: str
    percent: float = Field(ge=0, le=100)


class Sitelink(Strict):
    text: str = Field(max_length=25)
    url: str


class CampaignDraft(Strict):
    campaign_name: str
    bidding_strategy: Literal["MAXIMIZE_CONVERSIONS", "MAXIMIZE_CLICKS"]
    budget_allocation: list[BudgetShare] = Field(default_factory=list)
    ad_groups: list[AdGroupDraft] = Field(min_length=1)
    campaign_negatives: list[str] = Field(default_factory=list)
    sitelinks: list[Sitelink] = Field(default_factory=list)
    callouts: list[str] = Field(default_factory=list)
    use_call_asset: bool = True
    ad_schedule_note: str = ""
    location_note: str = ""
    rationale: str = ""


class AdFix(Strict):
    ads: list[AdCopy]
    changes: list[str] = Field(default_factory=list)


class Objection(Strict):
    category: Literal["UNSUPPORTED_CLAIM", "WEAK_INTENT", "IRRELEVANT_KEYWORD", "KEYWORD_OVERLAP", "BUDGET",
                      "LANDING_PAGE", "AD_MESSAGING", "POLICY", "MISSING_EXCLUSION", "MEASUREMENT", "OTHER"]
    severity: Literal["LOW", "MEDIUM", "HIGH"]
    target: str = ""
    message: str
    suggestion: str = ""


class ChallengeReport(Strict):
    verdict: Literal["APPROVE", "REVISE", "REJECT"]
    objections: list[Objection] = Field(default_factory=list)
    agreements: list[str] = Field(default_factory=list)


class Disagreement(Strict):
    topic: str
    human: str
    ai: str
    recommended_resolution: str


class HumanPlanResolution(Strict):
    disagreements: list[Disagreement] = Field(default_factory=list)
    summary: str = ""


DIAGNOSIS_KEYS = Literal["MEASUREMENT", "BUDGET_LIMITED", "RANK_LIMITED", "LOW_CTR", "LOW_CVR", "CALLS_MISSED",
                         "HIGH_CPC", "OPEN_CLIENT_FIXES"]


class DiagnosisCauses(Strict):
    causes: list[DIAGNOSIS_KEYS] = Field(default_factory=list)
    explanation: str = ""


class TextSummary(Strict):
    summary: str
    highlights: list[str] = Field(default_factory=list)


class AgentIntent(Strict):
    intent: str = ""
    reason: str = ""


SCHEMAS = {
    "AGENT_INTENT": AgentIntent,
    "EXTRACT_SERVICES": ServiceExtraction,
    "LABEL_KEYWORDS": KeywordLabels,
    "EXTRACT_COMPETITOR_ADS": CompetitorAdsExtraction,
    "DRAFT_CAMPAIGN": CampaignDraft,
    "DRAFT_AD_GROUP": AdGroupDraft,
    "DRAFT_AD": AdCopy,
    "FIX_ADS": AdFix,
    "CHALLENGE": ChallengeReport,
    "RESOLVE_HUMAN_PLAN": HumanPlanResolution,
    "DIAGNOSE": DiagnosisCauses,
    "REPORT_SUMMARY": TextSummary,
    "EXPLAIN": TextSummary,
    "AUDIT_SUMMARY": TextSummary,
}
