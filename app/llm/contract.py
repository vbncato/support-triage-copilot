"""Structured recommendation contract: JSON schema sent to the model and the app-side validator.

The model's output is never trusted. `validate_output` re-checks every rule, including the ones the
JSON schema already constrains, because demo recordings and a misbehaving provider bypass the schema.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.config import CATEGORIES, DISPOSITIONS, REQUESTABLE_DETAILS, queue_for

DRAFT_MAX = 4000
REASON_MAX = 500

# Sent as output_config.format.schema. Only features the structured-outputs docs list as supported:
# enum, anyOf with null, additionalProperties: false, every property required. No length constraints.
OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "disposition": {"type": "string", "enum": list(DISPOSITIONS)},
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "draft_reply": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "source_ids": {"type": "array", "items": {"type": "string"}},
        "requested_details": {
            "type": "array",
            "items": {"type": "string", "enum": list(REQUESTABLE_DETAILS)},
        },
        "reason": {"type": "string"},
    },
    "required": [
        "disposition",
        "category",
        "draft_reply",
        "source_ids",
        "requested_details",
        "reason",
    ],
    "additionalProperties": False,
}

# A sentence that asks the customer to send a secret. Negated sentences ("never share your API key")
# are allowed because the knowledge base tells operators to say exactly that.
_SECRET_NOUN = r"(password|passcode|api[ -]?key|secret|access[ -]?token|refresh[ -]?token|bearer[ -]?token|private[ -]?key|client[ -]?secret)"
_ASK_VERB = r"(send|share|provide|paste|include|attach|forward|give|reply with|tell us)"
_ASK_SECRET = re.compile(rf"\b{_ASK_VERB}\b[^.?!\n]{{0,60}}\b{_SECRET_NOUN}", re.IGNORECASE)
_NEGATION = re.compile(r"\b(do not|don't|never|not|no need|without)\b", re.IGNORECASE)


@dataclass
class ValidationResult:
    proposal: dict | None
    errors: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _err(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def asks_for_secret(text: str) -> bool:
    for sentence in re.split(r"(?<=[.?!])\s+|\n+", text):
        if _ASK_SECRET.search(sentence) and not _NEGATION.search(sentence):
            return True
    return False


def _norm_enum(value: object, allowed: tuple[str, ...]) -> str | None:
    # The structured-outputs docs warn that enum casing is not guaranteed; compare case-insensitively.
    if not isinstance(value, str):
        return None
    lookup = {a.lower(): a for a in allowed}
    return lookup.get(value.strip().lower())


def validate_output(raw_text: str, provided_ids: list[str]) -> ValidationResult:
    """Parse and validate a model output against the contract and the IDs actually provided."""
    try:
        data = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError) as exc:
        return ValidationResult(None, [_err("INVALID_JSON", f"Output is not valid JSON: {exc}")])
    if not isinstance(data, dict):
        return ValidationResult(None, [_err("INVALID_JSON", "Output must be a JSON object")])

    errors: list[dict] = []
    expected = set(OUTPUT_SCHEMA["required"])
    missing = expected - data.keys()
    extra = data.keys() - expected
    if missing:
        errors.append(_err("MISSING_FIELD", f"Missing fields: {', '.join(sorted(missing))}"))
    if extra:
        errors.append(_err("UNEXPECTED_FIELD", f"Unexpected fields: {', '.join(sorted(extra))}"))

    disposition = _norm_enum(data.get("disposition"), DISPOSITIONS)
    if disposition is None:
        errors.append(_err("BAD_DISPOSITION", f"Unknown disposition: {data.get('disposition')!r}"))
    category = _norm_enum(data.get("category"), CATEGORIES)
    if category is None:
        errors.append(_err("BAD_CATEGORY", f"Unknown category: {data.get('category')!r}"))

    draft = data.get("draft_reply")
    if draft is not None and not isinstance(draft, str):
        errors.append(_err("BAD_DRAFT", "draft_reply must be a string or null"))
        draft = None
    draft = draft.strip() if isinstance(draft, str) else None
    if draft == "":
        draft = None
    if draft and len(draft) > DRAFT_MAX:
        errors.append(_err("DRAFT_TOO_LONG", f"draft_reply exceeds {DRAFT_MAX} characters"))

    reason = data.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        errors.append(_err("MISSING_REASON", "reason must be a non-empty string"))
        reason = ""
    reason = reason.strip()[:REASON_MAX]

    source_ids = data.get("source_ids")
    if not isinstance(source_ids, list) or not all(isinstance(s, str) for s in source_ids):
        errors.append(_err("BAD_SOURCES", "source_ids must be a list of strings"))
        source_ids = []
    source_ids = list(dict.fromkeys(s.strip() for s in source_ids))
    unknown = [s for s in source_ids if s not in provided_ids]
    if unknown:
        errors.append(
            _err(
                "UNKNOWN_SOURCE",
                "Cited sources were not among the sections given to the model: " + ", ".join(unknown),
            )
        )

    details_raw = data.get("requested_details")
    if not isinstance(details_raw, list):
        errors.append(_err("BAD_DETAILS", "requested_details must be a list"))
        details_raw = []
    details: list[str] = []
    for d in details_raw:
        nd = _norm_enum(d, REQUESTABLE_DETAILS)
        if nd is None:
            errors.append(_err("BAD_DETAIL", f"Detail is not in the allowed set: {d!r}"))
        elif nd not in details:
            details.append(nd)

    if disposition == "DRAFT_REPLY":
        if not draft:
            errors.append(_err("DRAFT_REQUIRED", "DRAFT_REPLY requires draft_reply text"))
        if not source_ids:
            errors.append(_err("SOURCE_REQUIRED", "DRAFT_REPLY requires at least one source"))
    elif disposition == "NEEDS_INFO":
        if not details:
            errors.append(_err("DETAILS_REQUIRED", "NEEDS_INFO requires requested_details"))
        if not draft:
            errors.append(_err("DRAFT_REQUIRED", "NEEDS_INFO requires a draft question"))
    elif disposition == "MANUAL_REVIEW":
        draft = None  # no customer reply is prepared for manual review

    if draft and asks_for_secret(draft):
        errors.append(
            _err("SECRET_REQUEST", "Draft asks the customer for a password, key, token or secret")
        )

    proposal = {
        "disposition": disposition,
        "category": category,
        "queue": queue_for(category) if category else None,
        "draft_reply": draft,
        "source_ids": source_ids,
        "requested_details": details,
        "reason": reason,
    }
    return ValidationResult(proposal, errors)
