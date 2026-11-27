"""Adapter interface shared by the demo and live model modes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# Error types stored on failed attempts and shown to the operator.
ERROR_TYPES = {
    "TIMEOUT": "The model did not answer within the configured timeout.",
    "HTTP_ERROR": "The model provider returned an HTTP error.",
    "CONNECTION_ERROR": "The model provider could not be reached.",
    "PROVIDER_ERROR": "The model provider returned an unexpected response.",
    "MODEL_REFUSAL": "The model declined to answer this ticket.",
    "TRUNCATED": "The model output was cut off at the token limit.",
    "INVALID_JSON": "The model output was not valid JSON.",
    "CONTRACT_VIOLATION": "The model output broke the recommendation contract.",
    "CONFIG_ERROR": "Live mode is not configured (for example, LLM_API_KEY is empty).",
    "NO_DEMO_RECORDING": "Demo mode has no recorded model response for this ticket.",
    "ATTEMPT_EXPIRED": "The attempt passed its deadline without a result (process stopped or hung).",
    "INTERNAL_ERROR": "The application failed while running the analysis.",
}


class LLMError(Exception):
    def __init__(self, error_type: str, detail: str = ""):
        assert error_type in ERROR_TYPES, error_type
        super().__init__(f"{error_type}: {detail}")
        self.error_type = error_type
        self.detail = detail[:500]


@dataclass(frozen=True)
class ModelRequest:
    subject: str
    body: str
    system: str
    user: str
    attempt_no: int


@dataclass(frozen=True)
class ModelOutput:
    text: str
    model: str
    stop_reason: str | None = None


class ModelAdapter(Protocol):
    mode: str
    model: str

    def complete(self, request: ModelRequest) -> ModelOutput: ...
