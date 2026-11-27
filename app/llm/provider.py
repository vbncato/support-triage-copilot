"""Live mode: one provider, Anthropic Messages API via the official `anthropic` Python SDK.

Structured output uses `output_config.format` with a JSON schema (Anthropic structured-outputs docs).
The SDK's own retries are off by default (LLM_MAX_RETRIES=0) so LLM_TIMEOUT_SECONDS bounds the wait;
the operator retries explicitly. The API key is passed explicitly from LLM_API_KEY: the SDK must not
pick up credentials from the environment that belong to other projects.
"""

from __future__ import annotations

import anthropic

from app.config import Settings
from app.llm.base import LLMError, ModelOutput, ModelRequest
from app.llm.contract import OUTPUT_SCHEMA


def _scrub(text: str, secret: str) -> str:
    return text.replace(secret, "[redacted]") if secret else text


class AnthropicAdapter:
    mode = "live"

    def __init__(self, settings: Settings):
        if not settings.llm_api_key:
            raise LLMError("CONFIG_ERROR", "MODEL_MODE=live but LLM_API_KEY is empty")
        self.model = settings.llm_model
        self._settings = settings
        self._key = settings.llm_api_key
        self._client = anthropic.Anthropic(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    def complete(self, request: ModelRequest) -> ModelOutput:
        s = self._settings
        try:
            resp = self._client.messages.create(
                model=s.llm_model,
                max_tokens=s.llm_max_tokens,
                system=request.system,
                messages=[{"role": "user", "content": request.user}],
                output_config={
                    "effort": s.llm_effort,
                    "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
                },
            )
        except anthropic.APITimeoutError:
            raise LLMError("TIMEOUT", f"No answer within {s.llm_timeout_seconds:g}s") from None
        except anthropic.APIConnectionError as exc:
            raise LLMError("CONNECTION_ERROR", _scrub(str(exc), self._key)) from None
        except anthropic.APIStatusError as exc:
            request_id = exc.response.headers.get("request-id", "")
            raise LLMError(
                "HTTP_ERROR",
                _scrub(f"HTTP {exc.status_code} {type(exc).__name__} request-id={request_id}", self._key),
            ) from None
        except anthropic.APIError as exc:
            raise LLMError("PROVIDER_ERROR", _scrub(type(exc).__name__, self._key)) from None

        # Check stop_reason before parsing: refusals and truncation may not match the schema.
        if resp.stop_reason == "refusal":
            category = getattr(getattr(resp, "stop_details", None), "category", None)
            raise LLMError("MODEL_REFUSAL", f"stop_reason=refusal category={category}")
        if resp.stop_reason == "max_tokens":
            raise LLMError("TRUNCATED", f"Output hit max_tokens={s.llm_max_tokens}")
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        if not text.strip():
            raise LLMError("PROVIDER_ERROR", f"No text block in response (stop_reason={resp.stop_reason})")
        return ModelOutput(text, resp.model, resp.stop_reason)


class UnavailableAdapter:
    """Live mode without a usable configuration: every call fails with a clear, stored error."""

    mode = "live"

    def __init__(self, model: str, error: LLMError):
        self.model = model
        self._error = error

    def complete(self, request: ModelRequest) -> ModelOutput:
        raise LLMError(self._error.error_type, self._error.detail)


def build_adapter(settings: Settings):
    if settings.model_mode == "live":
        try:
            return AnthropicAdapter(settings)
        except LLMError as exc:
            return UnavailableAdapter(settings.llm_model, exc)
    from app.llm.demo import DemoAdapter

    return DemoAdapter(settings.llm_timeout_seconds)
