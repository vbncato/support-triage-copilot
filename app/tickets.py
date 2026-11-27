"""Ticket state, analysis attempts and operator decisions.

Every state change locks the ticket row (SELECT ... FOR UPDATE), checks the version the caller saw,
updates the ticket and writes its history event in the same transaction. Lock order is always
ticket -> attempt. No transaction in this module is held open across a model call.

Ticket states and allowed transitions (see README):
  NEW, READY_FOR_REVIEW, MANUAL_REVIEW, ANALYSIS_FAILED --start analysis--> ANALYZING
  ANALYZING --valid DRAFT_REPLY / NEEDS_INFO--> READY_FOR_REVIEW
  ANALYZING --valid MANUAL_REVIEW or rule NO_KB_MATCH--> MANUAL_REVIEW
  ANALYZING --error, invalid output, expired attempt--> ANALYSIS_FAILED
  NEW, ANALYZING, READY_FOR_REVIEW, ANALYSIS_FAILED --operator: route to manual--> MANUAL_REVIEW
  READY_FOR_REVIEW --operator: approve--> RESOLVED
  READY_FOR_REVIEW, MANUAL_REVIEW, ANALYSIS_FAILED --operator: edit and approve--> RESOLVED
  RESOLVED is final.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

import psycopg

from app.config import BODY_MAX, EXTERNAL_ID_MAX, FINAL_TEXT_MAX, SUBJECT_MAX

STATUSES = ("NEW", "ANALYZING", "READY_FOR_REVIEW", "MANUAL_REVIEW", "ANALYSIS_FAILED", "RESOLVED")
ANALYZABLE = ("NEW", "READY_FOR_REVIEW", "MANUAL_REVIEW", "ANALYSIS_FAILED")
ROUTABLE_TO_MANUAL = ("NEW", "ANALYZING", "READY_FOR_REVIEW", "ANALYSIS_FAILED")
EDIT_APPROVABLE = ("READY_FOR_REVIEW", "MANUAL_REVIEW", "ANALYSIS_FAILED")
APPROVABLE_OUTCOMES = ("DRAFT_REPLY", "NEEDS_INFO")


class ConflictError(Exception):
    """Maps to HTTP 409. The operator should reload the ticket."""

    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


class NotFoundError(Exception):
    pass


class InvalidInput(Exception):
    """Maps to HTTP 422."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class AttemptResult:
    state: str  # SUCCEEDED | FAILED
    model: str
    decided_by: str | None = None  # model | rule
    outcome: str | None = None
    proposal: dict | None = None
    raw_output: str | None = None
    error_type: str | None = None
    error_detail: str | None = None
    validation_errors: list[dict] = field(default_factory=list)


def _event(conn, ticket_id, actor, action, from_status=None, to_status=None, attempt_id=None, detail=None):
    conn.execute(
        """INSERT INTO ticket_events (ticket_id, actor, action, from_status, to_status, attempt_id, detail)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (ticket_id, actor, action, from_status, to_status, attempt_id, json.dumps(detail or {})),
    )


def _lock_ticket(conn, ticket_id: int) -> dict:
    t = conn.execute("SELECT * FROM tickets WHERE id = %s FOR UPDATE", (ticket_id,)).fetchone()
    if t is None:
        raise NotFoundError(ticket_id)
    return t


def _check_version(t: dict, expected_version: int | None) -> None:
    if t["status"] == "RESOLVED":
        raise ConflictError(
            "ALREADY_RESOLVED",
            "This ticket already has a final decision. Reload to see it.",
            current_version=t["version"],
            status=t["status"],
        )
    if expected_version is None:
        raise InvalidInput("VERSION_REQUIRED", "expected_version is required")
    if t["version"] != expected_version:
        raise ConflictError(
            "STALE_VERSION",
            f"The ticket changed since you loaded it (you saw version {expected_version}, "
            f"current is {t['version']}). Reload the ticket and try again.",
            current_version=t["version"],
            status=t["status"],
        )


def _set_status(conn, t: dict, new_status: str, **cols) -> int:
    sets = ["status = %(status)s", "version = version + 1", "updated_at = now()"]
    params = {"status": new_status, "id": t["id"]}
    for k, v in cols.items():
        sets.append(f"{k} = %({k})s")
        params[k] = v
    row = conn.execute(
        f"UPDATE tickets SET {', '.join(sets)} WHERE id = %(id)s RETURNING version", params
    ).fetchone()
    return row["version"]


# --- tickets ----------------------------------------------------------------------------------


def validate_ticket_input(subject: str, body: str, external_id: str | None) -> tuple[str, str, str | None]:
    subject = (subject or "").strip()
    body = (body or "").strip()
    if not subject:
        raise InvalidInput("EMPTY_SUBJECT", "subject must not be empty")
    if not body:
        raise InvalidInput("EMPTY_BODY", "body must not be empty")
    if len(subject) > SUBJECT_MAX:
        raise InvalidInput("SUBJECT_TOO_LONG", f"subject must be at most {SUBJECT_MAX} characters")
    if len(body) > BODY_MAX:
        raise InvalidInput("BODY_TOO_LONG", f"body must be at most {BODY_MAX} characters")
    if external_id is not None:
        external_id = external_id.strip() or None
    if external_id and len(external_id) > EXTERNAL_ID_MAX:
        raise InvalidInput("EXTERNAL_ID_TOO_LONG", f"external_id must be at most {EXTERNAL_ID_MAX} characters")
    return subject, body, external_id


def create_ticket(conn, subject: str, body: str, external_id: str | None = None) -> dict:
    subject, body, external_id = validate_ticket_input(subject, body, external_id)
    with conn.transaction():
        try:
            t = conn.execute(
                """INSERT INTO tickets (external_id, subject, body, status) VALUES (%s, %s, %s, 'NEW')
                   RETURNING *""",
                (external_id, subject, body),
            ).fetchone()
        except psycopg.errors.UniqueViolation:
            raise ConflictError("DUPLICATE_EXTERNAL_ID", f"A ticket with external_id {external_id!r} already exists") from None
        _event(conn, t["id"], "system", "TICKET_CREATED", to_status="NEW", detail={"source": "api"})
    return t


def list_tickets(conn, status: str | None = None) -> list[dict]:
    return conn.execute(
        """
        SELECT t.id, t.external_id, t.subject, t.status, t.version, t.created_at, t.updated_at,
               a.proposal->>'category' AS category, a.proposal->>'queue' AS queue,
               a.outcome, a.mode
        FROM tickets t
        LEFT JOIN analysis_attempts a ON a.id = t.current_attempt_id
        WHERE (%(s)s::text IS NULL OR t.status = %(s)s)
        ORDER BY t.id DESC
        """,
        {"s": status},
    ).fetchall()


# --- attempts ---------------------------------------------------------------------------------


def expire_stale_attempts(conn, now: datetime | None = None) -> int:
    """Expire RUNNING attempts past their deadline (crashed or hung process).

    The ticket moves ANALYZING -> ANALYSIS_FAILED when the expired attempt is its current one, so the
    operator can retry or route to manual review. Runs on startup, on ticket reads and before analysis.
    """
    expired = 0
    with conn.transaction():
        rows = conn.execute(
            """SELECT a.id, a.ticket_id FROM analysis_attempts a
               WHERE a.state = 'RUNNING' AND a.deadline_at < COALESCE(%s::timestamptz, now())
               ORDER BY a.ticket_id""",
            (now,),
        ).fetchall()
    for r in rows:
        with conn.transaction():
            t = _lock_ticket(conn, r["ticket_id"])
            a = conn.execute(
                "SELECT * FROM analysis_attempts WHERE id = %s FOR UPDATE", (r["id"],)
            ).fetchone()
            if a["state"] != "RUNNING":
                continue  # finished meanwhile
            conn.execute(
                """UPDATE analysis_attempts SET state = 'EXPIRED', finished_at = now(),
                   error_type = 'ATTEMPT_EXPIRED',
                   error_detail = 'No result before the attempt deadline; the process may have stopped.'
                   WHERE id = %s""",
                (a["id"],),
            )
            expired += 1
            if t["current_attempt_id"] == a["id"] and t["status"] == "ANALYZING":
                _set_status(conn, t, "ANALYSIS_FAILED")
                _event(conn, t["id"], "system", "ATTEMPT_EXPIRED", "ANALYZING", "ANALYSIS_FAILED", a["id"])
            else:
                _event(conn, t["id"], "system", "ATTEMPT_EXPIRED", t["status"], t["status"], a["id"])
    return expired


def start_attempt(
    conn,
    ticket_id: int,
    expected_version: int | None,
    *,
    mode: str,
    model: str,
    deadline_seconds: float,
    retrieve,
) -> tuple[dict, dict]:
    """Create a RUNNING attempt and move the ticket to ANALYZING. Returns (attempt, ticket).

    `retrieve(subject, body)` runs inside this transaction (a DB query, no network) and returns the
    sections that will be given to the model; they are stored on the attempt.
    """
    expire_stale_attempts(conn)
    with conn.transaction():
        t = _lock_ticket(conn, ticket_id)
        if t["status"] == "ANALYZING":
            if expected_version is not None and t["version"] != expected_version:
                _check_version(t, expected_version)
            raise ConflictError(
                "ANALYSIS_IN_PROGRESS",
                "An analysis is already running for this ticket. Wait for it or route to manual review.",
                current_version=t["version"],
                status=t["status"],
            )
        _check_version(t, expected_version)
        if t["status"] not in ANALYZABLE:
            raise ConflictError("NOT_ANALYZABLE", f"Cannot analyze a ticket in status {t['status']}")
        sections = retrieve(t["subject"], t["body"])
        n = conn.execute(
            "SELECT COALESCE(MAX(attempt_no), 0) + 1 AS n FROM analysis_attempts WHERE ticket_id = %s",
            (ticket_id,),
        ).fetchone()["n"]
        a = conn.execute(
            """INSERT INTO analysis_attempts (ticket_id, attempt_no, mode, model, state, deadline_at, retrieved)
               VALUES (%s, %s, %s, %s, 'RUNNING', now() + make_interval(secs => %s), %s)
               RETURNING *""",
            (
                ticket_id,
                n,
                mode,
                model,
                deadline_seconds,
                json.dumps([{"id": s.id, "title": s.title, "rank": s.rank} for s in sections]),
            ),
        ).fetchone()
        from_status = t["status"]
        version = _set_status(conn, t, "ANALYZING", current_attempt_id=a["id"])
        _event(conn, ticket_id, "system", "ANALYSIS_STARTED", from_status, "ANALYZING", a["id"],
               {"attempt_no": n, "mode": mode, "sources_given": [s.id for s in sections]})
    t = dict(t, status="ANALYZING", version=version, current_attempt_id=a["id"])
    return a, t


def finish_attempt(conn, attempt_id: int, result: AttemptResult) -> bool:
    """Record an attempt's result. Returns False if the result arrived too late to apply.

    The result is applied to the ticket only if the attempt is still RUNNING, is still the ticket's
    current attempt and the ticket is still ANALYZING. Otherwise (a newer attempt started, an
    operator acted, or the attempt expired) it is stored in `late_result` and the ticket is untouched.
    """
    with conn.transaction():
        ticket_id = conn.execute(
            "SELECT ticket_id FROM analysis_attempts WHERE id = %s", (attempt_id,)
        ).fetchone()["ticket_id"]
        t = _lock_ticket(conn, ticket_id)
        a = conn.execute("SELECT * FROM analysis_attempts WHERE id = %s FOR UPDATE", (attempt_id,)).fetchone()
        payload = {
            "state": result.state,
            "outcome": result.outcome,
            "error_type": result.error_type,
            "proposal": result.proposal,
            "validation_errors": result.validation_errors,
        }
        if a["state"] != "RUNNING" or t["current_attempt_id"] != a["id"] or t["status"] != "ANALYZING":
            conn.execute(
                "UPDATE analysis_attempts SET late_result = %s, raw_output = COALESCE(raw_output, %s) WHERE id = %s",
                (json.dumps(payload), result.raw_output, attempt_id),
            )
            _event(conn, ticket_id, "system", "LATE_RESULT_IGNORED", t["status"], t["status"], attempt_id,
                   {"attempt_state": a["state"], "result_state": result.state})
            return False
        conn.execute(
            """UPDATE analysis_attempts SET state = %s, finished_at = now(), model = %s, decided_by = %s,
                   outcome = %s, proposal = %s, raw_output = %s, error_type = %s, error_detail = %s,
                   validation_errors = %s
               WHERE id = %s""",
            (
                result.state,
                result.model,
                result.decided_by,
                result.outcome,
                json.dumps(result.proposal) if result.proposal is not None else None,
                result.raw_output,
                result.error_type,
                result.error_detail,
                json.dumps(result.validation_errors),
                attempt_id,
            ),
        )
        if result.state == "SUCCEEDED":
            new_status = "MANUAL_REVIEW" if result.outcome == "MANUAL_REVIEW" else "READY_FOR_REVIEW"
            action = "ANALYSIS_SUCCEEDED"
        else:
            new_status = "ANALYSIS_FAILED"
            action = "ANALYSIS_FAILED"
        _set_status(conn, t, new_status)
        _event(conn, ticket_id, "system", action, "ANALYZING", new_status, attempt_id,
               {"outcome": result.outcome, "error_type": result.error_type, "decided_by": result.decided_by})
        return True


# --- operator decisions -----------------------------------------------------------------------


def route_manual(conn, ticket_id: int, expected_version: int | None, note: str | None = None) -> None:
    with conn.transaction():
        t = _lock_ticket(conn, ticket_id)
        _check_version(t, expected_version)
        if t["status"] not in ROUTABLE_TO_MANUAL:
            raise ConflictError("NOT_ALLOWED", f"Cannot route a ticket in status {t['status']} to manual review")
        if t["status"] == "ANALYZING" and t["current_attempt_id"]:
            conn.execute(
                """UPDATE analysis_attempts SET state = 'SUPERSEDED', finished_at = now()
                   WHERE id = %s AND state = 'RUNNING'""",
                (t["current_attempt_id"],),
            )
        _set_status(conn, t, "MANUAL_REVIEW")
        _event(conn, ticket_id, "operator", "ROUTED_TO_MANUAL_REVIEW", t["status"], "MANUAL_REVIEW",
               t["current_attempt_id"], {"note": (note or "").strip()[:500]})


def _current_attempt(conn, t: dict) -> dict | None:
    if not t["current_attempt_id"]:
        return None
    return conn.execute("SELECT * FROM analysis_attempts WHERE id = %s", (t["current_attempt_id"],)).fetchone()


def _insert_decision(conn, t, action, attempt, final_text, edited) -> None:
    valid = bool(attempt) and attempt["state"] == "SUCCEEDED" and not attempt["validation_errors"]
    proposal = (attempt["proposal"] or {}) if valid else {}
    try:
        with conn.transaction():  # savepoint: a duplicate must not poison the outer transaction
            conn.execute(
                """INSERT INTO decisions (ticket_id, action, attempt_id, final_text, edited, disposition,
                                          category, queue, ticket_version)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (t["id"], action, attempt["id"] if attempt else None, final_text, edited,
                 proposal.get("disposition"), proposal.get("category"), proposal.get("queue"), t["version"]),
            )
    except psycopg.errors.UniqueViolation:
        raise ConflictError("ALREADY_RESOLVED", "This ticket already has a final decision.") from None


def approve(conn, ticket_id: int, expected_version: int | None, attempt_id: int | None) -> None:
    """Approve the current, valid draft exactly as proposed."""
    with conn.transaction():
        t = _lock_ticket(conn, ticket_id)
        _check_version(t, expected_version)
        if t["status"] != "READY_FOR_REVIEW":
            raise ConflictError("NOT_ALLOWED", f"Only READY_FOR_REVIEW tickets can be approved (status is {t['status']})")
        a = _current_attempt(conn, t)
        if a is None or attempt_id != a["id"]:
            raise ConflictError("STALE_ATTEMPT", "The draft you saw is no longer the current one. Reload the ticket.",
                                current_version=t["version"])
        proposal = a["proposal"] or {}
        if a["state"] != "SUCCEEDED" or a["validation_errors"] or a["outcome"] not in APPROVABLE_OUTCOMES \
                or not proposal.get("draft_reply"):
            raise ConflictError("DRAFT_NOT_APPROVABLE", "The current analysis has no valid draft to approve.")
        _insert_decision(conn, t, "APPROVE", a, proposal["draft_reply"], edited=False)
        _set_status(conn, t, "RESOLVED")
        _event(conn, ticket_id, "operator", "APPROVED", t["status"], "RESOLVED", a["id"],
               {"disposition": a["outcome"]})


def edit_approve(conn, ticket_id: int, expected_version: int | None, attempt_id: int | None, final_text: str) -> None:
    """Save the operator's own (edited or newly written) text as the final reply."""
    final_text = (final_text or "").strip()
    if not final_text:
        raise InvalidInput("EMPTY_FINAL_TEXT", "final_text must not be empty")
    if len(final_text) > FINAL_TEXT_MAX:
        raise InvalidInput("FINAL_TEXT_TOO_LONG", f"final_text must be at most {FINAL_TEXT_MAX} characters")
    with conn.transaction():
        t = _lock_ticket(conn, ticket_id)
        _check_version(t, expected_version)
        if t["status"] not in EDIT_APPROVABLE:
            raise ConflictError("NOT_ALLOWED", f"Cannot approve a ticket in status {t['status']}")
        a = _current_attempt(conn, t)
        if attempt_id is not None and (a is None or attempt_id != a["id"]):
            raise ConflictError("STALE_ATTEMPT", "The draft you saw is no longer the current one. Reload the ticket.",
                                current_version=t["version"])
        draft = ((a or {}).get("proposal") or {}).get("draft_reply") if a and not a["validation_errors"] else None
        edited = final_text != (draft or "")
        _insert_decision(conn, t, "EDIT_APPROVE", a, final_text, edited=edited)
        _set_status(conn, t, "RESOLVED")
        _event(conn, ticket_id, "operator", "EDITED_AND_APPROVED", t["status"], "RESOLVED",
               a["id"] if a else None, {"edited": edited, "length": len(final_text)})


# --- read model -------------------------------------------------------------------------------


def get_ticket_detail(conn, ticket_id: int) -> dict:
    expire_stale_attempts(conn)
    t = conn.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,)).fetchone()
    if t is None:
        raise NotFoundError(ticket_id)
    attempts = conn.execute(
        """SELECT id, attempt_no, mode, model, state, started_at, deadline_at, finished_at, decided_by,
                  outcome, error_type, (late_result IS NOT NULL) AS late
           FROM analysis_attempts WHERE ticket_id = %s ORDER BY attempt_no DESC""",
        (ticket_id,),
    ).fetchall()
    current = _current_attempt(conn, t)
    if current:
        given = {r["id"]: r for r in current["retrieved"]}
        rows = conn.execute(
            "SELECT id, title, body FROM kb_sections WHERE id = ANY(%s)", (list(given),)
        ).fetchall()
        bodies = {r["id"]: r for r in rows}
        cited = set(((current["proposal"] or {}).get("source_ids")) or [])
        current = dict(current)
        current["sources"] = [
            {
                "id": sid,
                "title": (bodies.get(sid) or {}).get("title", given[sid]["title"]),
                "body": (bodies.get(sid) or {}).get("body", ""),
                "rank": given[sid]["rank"],
                "url": f"/kb/{sid}",
                "cited": sid in cited,
            }
            for sid in given
        ]
        current["invalid_citations"] = sorted(cited - set(given))
    events = conn.execute(
        "SELECT * FROM ticket_events WHERE ticket_id = %s ORDER BY id", (ticket_id,)
    ).fetchall()
    decision = conn.execute("SELECT * FROM decisions WHERE ticket_id = %s", (ticket_id,)).fetchone()
    approvable = bool(
        current
        and t["status"] == "READY_FOR_REVIEW"
        and current["state"] == "SUCCEEDED"
        and not current["validation_errors"]
        and current["outcome"] in APPROVABLE_OUTCOMES
        and (current["proposal"] or {}).get("draft_reply")
    )
    return {
        "ticket": t,
        "current_attempt": current,
        "attempts": attempts,
        "events": events,
        "decision": decision,
        "actions": {
            "analyze": t["status"] in ANALYZABLE,
            "approve": approvable,
            "edit_approve": t["status"] in EDIT_APPROVABLE,
            "route_manual": t["status"] in ROUTABLE_TO_MANUAL,
        },
    }
