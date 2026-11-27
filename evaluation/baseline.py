"""Keyword baseline for routing. Deterministic, no model.

Each category has a list of keyword patterns. The category with the most matches wins; ties go to
the category listed first; no match gives OTHER. Tuned on the dev split only.
"""

from __future__ import annotations

import re

from app.config import queue_for

KEYWORDS: dict[str, list[str]] = {
    "AUTHENTICATION": [r"\b401\b", r"unauthori[sz]ed", r"token_expired", r"invalid_token",
                       r"missing_credentials", r"\btoken", r"authenticat"],
    "PERMISSIONS": [r"\b403\b", r"forbidden", r"insufficient_scope", r"\bscope", r"\brole\b",
                    r"permission", r"workspace_mismatch"],
    "RATE_LIMIT": [r"\b429\b", r"rate limit", r"too many requests", r"retry-after", r"throttl",
                   r"requests per second", r"throughput"],
    "WEBHOOK_DELIVERY": [r"webhook", r"\bevt_", r"signature", r"endpoint", r"\bevents?\b"],
    "PROCESSING_DELAY": [r"\bexport", r"\bbatch", r"\bjob_", r"queued", r"stuck", r"progress_percent",
                         r"\bslow"],
}
_COMPILED = {c: [re.compile(p, re.IGNORECASE) for p in pats] for c, pats in KEYWORDS.items()}


def classify(subject: str, body: str) -> str:
    text = f"{subject}\n{body}"
    best, best_score = "OTHER", 0
    for category, patterns in _COMPILED.items():
        score = sum(1 for p in patterns if p.search(text))
        if score > best_score:
            best, best_score = category, score
    return best


def route(subject: str, body: str) -> tuple[str, str]:
    c = classify(subject, body)
    return c, queue_for(c)
