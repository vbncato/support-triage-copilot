"""Runtime settings, read once from environment variables.

Only LLM_API_KEY is secret. It is never logged, rendered or stored.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

# Closed category set. The model proposes a category; the queue is derived here, never by the model.
CATEGORY_QUEUE: dict[str, str] = {
    "AUTHENTICATION": "access-and-identity",
    "PERMISSIONS": "access-and-identity",
    "RATE_LIMIT": "api-platform",
    "WEBHOOK_DELIVERY": "integrations",
    "PROCESSING_DELAY": "data-pipeline",
    "OTHER": "general-support",
}
CATEGORIES: tuple[str, ...] = tuple(CATEGORY_QUEUE)

DISPOSITIONS: tuple[str, ...] = ("DRAFT_REPLY", "NEEDS_INFO", "MANUAL_REVIEW")

# Closed set of details the assistant may ask a customer for. Secrets cannot be expressed here.
REQUESTABLE_DETAILS: tuple[str, ...] = (
    "request_id",
    "timestamp",
    "http_status_code",
    "endpoint",
    "event_id",
    "webhook_endpoint_url",
    "account_id",
    "job_id",
    "error_response_body",
    "client_library_version",
)

SUBJECT_MAX = 200
BODY_MAX = 5000
EXTERNAL_ID_MAX = 64
FINAL_TEXT_MAX = 5000


def queue_for(category: str) -> str:
    return CATEGORY_QUEUE[category]


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, "").strip() or default)


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, "").strip() or default)


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    database_url: str
    model_mode: str
    llm_model: str
    llm_api_key: str = field(repr=False)
    llm_base_url: str | None
    llm_timeout_seconds: float
    llm_max_tokens: int
    llm_effort: str
    llm_max_retries: int
    attempt_grace_seconds: int
    retrieval_top_k: int
    retrieval_min_terms: int
    retrieval_max_chars: int
    seed_demo_tickets: bool

    @property
    def model_label(self) -> str:
        return self.llm_model if self.model_mode == "live" else "demo-recordings-v1"


def load_settings() -> Settings:
    mode = os.environ.get("MODEL_MODE", "demo").strip().lower() or "demo"
    if mode not in ("demo", "live"):
        raise ValueError("MODEL_MODE must be 'demo' or 'live'")
    effort = os.environ.get("LLM_EFFORT", "low").strip() or "low"
    if effort not in ("low", "medium", "high", "xhigh", "max"):
        raise ValueError("LLM_EFFORT must be one of low, medium, high, xhigh, max")
    return Settings(
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql://triage:triage-local-only@db:5432/triage"
        ),
        model_mode=mode,
        llm_model=os.environ.get("LLM_MODEL", "").strip() or "claude-opus-5-5",
        llm_api_key=os.environ.get("LLM_API_KEY", "").strip(),
        llm_base_url=os.environ.get("LLM_BASE_URL", "").strip() or None,
        llm_timeout_seconds=_float("LLM_TIMEOUT_SECONDS", 30.0),
        llm_max_tokens=_int("LLM_MAX_TOKENS", 4096),
        llm_effort=effort,
        llm_max_retries=_int("LLM_MAX_RETRIES", 0),
        attempt_grace_seconds=_int("ATTEMPT_GRACE_SECONDS", 10),
        retrieval_top_k=_int("RETRIEVAL_TOP_K", 4),
        retrieval_min_terms=_int("RETRIEVAL_MIN_TERMS", 2),
        retrieval_max_chars=_int("RETRIEVAL_MAX_CHARS", 1500),
        seed_demo_tickets=_bool("SEED_DEMO_TICKETS", True),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
