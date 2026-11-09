"""Ad-text rules shared by the validator: Google Ads RSA limits and a simplified editorial-policy check.

These are deterministic rules that run on any ad text (AI-drafted or user-edited). They are not data.
"""
from __future__ import annotations

import re

HEADLINE_MAX, DESCRIPTION_MAX, PATH_MAX = 30, 90, 15
ALLOWED_CAPS = {"HVAC", "USA", "DUI", "LLC", "24/7", "AC", "CPR", "ER", "DDS", "FAQ"}


def policy_issues_for_text(text: str, kind: str) -> list[str]:
    """Simplified subset of Google Ads editorial policy applied by our validator."""
    issues = []
    if kind == "headline" and "!" in text:
        issues.append(f"Headline '{text}': exclamation marks are not allowed in headlines")
    if "!!" in text or "??" in text:
        issues.append(f"'{text}': repeated punctuation is not allowed")
    for word in re.findall(r"[A-Za-z/0-9]{4,}", text):
        if word.isupper() and word.isalpha() and word not in ALLOWED_CAPS:
            issues.append(f"'{text}': excessive capitalisation ('{word}')")
            break
    if re.search(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", text):
        issues.append(f"'{text}': phone numbers are not allowed in ad text (use a call asset)")
    return issues
