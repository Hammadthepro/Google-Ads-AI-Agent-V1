"""Deterministic keyword-intent labelling used by the keyword audit (rule-based, no data)."""
from __future__ import annotations

from app.core.util import normalize_term

JUNK_WORDS = {"jobs", "job", "diy", "how", "free", "salary", "course", "training", "career", "school"}


def label_keyword(kw: str, services: list[str], excluded: list[str]) -> tuple[str, str | None, str]:
    words = set(normalize_term(kw).split())
    n = normalize_term(kw)
    for ex in excluded:
        if normalize_term(ex) and normalize_term(ex) in n:
            return "IRRELEVANT", None, f"matches excluded service '{ex}'"
    if words & JUNK_WORDS or n.startswith("how to"):
        return "IRRELEVANT", None, "job-seeker / DIY / free intent"
    best, best_score = None, 0.0
    for s in services:
        sw = set(normalize_term(s).split())
        score = len(sw & words) / max(1, len(sw))
        if score > best_score:
            best, best_score = s, score
    if best and best_score >= 0.99:
        return "SERVICE", best, "contains the full service name"
    if best and best_score >= 0.5:
        return "SERVICE", best, "partial service match"
    return "GENERIC", None, "category-level intent"
