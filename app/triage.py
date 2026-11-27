"""Run one analysis: retrieve sections, call the model adapter outside any transaction, validate,
and record the result against the specific attempt."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app import db, knowledge, tickets
from app.config import Settings
from app.knowledge import Section
from app.llm.base import ERROR_TYPES, LLMError, ModelRequest
from app.llm.contract import validate_output
from app.llm.prompt import SYSTEM_PROMPT, build_user_message
from app.tickets import AttemptResult

log = logging.getLogger("triage")

NO_KB_MATCH_REASON = (
    "Rule NO_KB_MATCH: no knowledge-base section matched this ticket above the relevance threshold, "
    "so the model was not called. An operator needs to handle it."
)


@dataclass
class CaseAnalysis:
    """Result of analysing one ticket text, independent of storage. Also used by evaluation."""

    result: AttemptResult
    sections: list[Section]


def analyze_text(subject: str, body: str, sections: list[Section], adapter, attempt_no: int) -> AttemptResult:
    provided = [s.id for s in sections]
    if not sections:
        return AttemptResult(
            state="SUCCEEDED",
            model="rule",
            decided_by="rule",
            outcome="MANUAL_REVIEW",
            proposal={
                "disposition": "MANUAL_REVIEW",
                "category": "OTHER",
                "queue": "general-support",
                "draft_reply": None,
                "source_ids": [],
                "requested_details": [],
                "reason": NO_KB_MATCH_REASON,
            },
        )
    request = ModelRequest(
        subject=subject,
        body=body,
        system=SYSTEM_PROMPT,
        user=build_user_message(
            subject, body, [{"id": s.id, "title": s.title, "text": s.body} for s in sections]
        ),
        attempt_no=attempt_no,
    )
    try:
        out = adapter.complete(request)
    except LLMError as exc:
        return AttemptResult(state="FAILED", model=adapter.model, error_type=exc.error_type,
                             error_detail=exc.detail or ERROR_TYPES[exc.error_type])
    except Exception as exc:  # never lose the ticket because of an adapter bug
        log.exception("adapter failure")
        return AttemptResult(state="FAILED", model=adapter.model, error_type="INTERNAL_ERROR",
                             error_detail=type(exc).__name__)
    v = validate_output(out.text, provided)
    if not v.ok:
        error_type = "INVALID_JSON" if v.proposal is None else "CONTRACT_VIOLATION"
        return AttemptResult(
            state="FAILED",
            model=out.model,
            decided_by="model",
            outcome=None,
            proposal=v.proposal,
            raw_output=out.text,
            error_type=error_type,
            error_detail="; ".join(e["message"] for e in v.errors)[:500],
            validation_errors=v.errors,
        )
    return AttemptResult(
        state="SUCCEEDED",
        model=out.model,
        decided_by="model",
        outcome=v.proposal["disposition"],
        proposal=v.proposal,
        raw_output=out.text,
    )


def retriever(conn, settings: Settings):
    def _retrieve(subject: str, body: str) -> list[Section]:
        return knowledge.search(
            conn,
            subject,
            body,
            top_k=settings.retrieval_top_k,
            min_terms=settings.retrieval_min_terms,
            max_chars=settings.retrieval_max_chars,
        )

    return _retrieve


def run_analysis(ticket_id: int, expected_version: int | None, settings: Settings, adapter) -> bool:
    """Start an attempt, call the model with no transaction open, then record the result.

    Returns True if the result was applied to the ticket, False if it arrived too late.
    Raises tickets.ConflictError / NotFoundError / InvalidInput before any model call.
    """
    captured: list[Section] = []
    with db.connection() as conn:
        search = retriever(conn, settings)

        def retrieve(subject: str, body: str) -> list[Section]:
            captured[:] = search(subject, body)
            return captured

        attempt, ticket = tickets.start_attempt(
            conn,
            ticket_id,
            expected_version,
            mode=adapter.mode,
            model=adapter.model,
            deadline_seconds=settings.llm_timeout_seconds + settings.attempt_grace_seconds,
            retrieve=retrieve,
        )
    # Connection returned to the pool: nothing is held while the model runs.
    sections = list(captured)  # exactly the sections recorded on the attempt
    result = analyze_text(ticket["subject"], ticket["body"], sections, adapter, attempt["attempt_no"])
    with db.connection() as conn:
        return tickets.finish_attempt(conn, attempt["id"], result)
