"""Ticket lifecycle against the real PostgreSQL test database."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from app import db, knowledge, tickets, triage
from app.llm.demo import DemoAdapter
from app.tickets import AttemptResult, ConflictError, InvalidInput

from .helpers import TIMEOUT, BlockingAdapter, ScriptedAdapter, draft, make_ticket, status, version

pytestmark = pytest.mark.db


def analyze(conn, settings, ticket_id, adapter):
    return triage.run_analysis(ticket_id, version(conn, ticket_id), settings, adapter)


def current_attempt(conn, ticket_id):
    return conn.execute(
        "SELECT a.* FROM analysis_attempts a JOIN tickets t ON t.current_attempt_id = a.id WHERE t.id = %s",
        (ticket_id,),
    ).fetchone()


# --- input ------------------------------------------------------------------------------------


@pytest.mark.parametrize("subject,body,code", [
    ("", "x", "EMPTY_SUBJECT"), ("   ", "x", "EMPTY_SUBJECT"), ("s", "\n\t ", "EMPTY_BODY"),
    ("s" * 201, "x", "SUBJECT_TOO_LONG"), ("s", "x" * 5001, "BODY_TOO_LONG"),
])
def test_ticket_input_validation(conn, subject, body, code):
    with pytest.raises(InvalidInput) as e:
        tickets.create_ticket(conn, subject, body)
    assert e.value.code == code


def test_ticket_is_stored_with_status_and_event(conn):
    t = make_ticket(conn, external_id="EXT-1")
    assert t["status"] == "NEW" and t["version"] == 1 and t["created_at"]
    with pytest.raises(ConflictError):
        make_ticket(conn, external_id="EXT-1")
    ev = conn.execute("SELECT action FROM ticket_events WHERE ticket_id = %s", (t["id"],)).fetchall()
    assert [e["action"] for e in ev] == ["TICKET_CREATED"]


# --- retrieval --------------------------------------------------------------------------------


def test_retrieval_finds_401_section_and_is_stable(conn, settings):
    a = knowledge.search(conn, "401 token_expired", "Every call returns 401", top_k=4, min_terms=1, max_chars=200)
    b = knowledge.search(conn, "401 token_expired", "Every call returns 401", top_k=4, min_terms=1, max_chars=200)
    assert a[0].id == "kb-auth-401"
    assert [s.id for s in a] == [s.id for s in b]
    assert len(a) <= 4 and all(len(s.body) <= 200 for s in a)
    ranks = [(-s.rank, s.id) for s in a]
    assert ranks == sorted(ranks)  # rank desc, id asc on ties


def test_retrieval_empty_when_nothing_matches(conn):
    assert knowledge.search(conn, "zzqx", "qqzx vvxq", top_k=4, min_terms=1, max_chars=100) == []


# --- analysis outcomes ------------------------------------------------------------------------


def test_401_draft_then_approve(conn, settings):
    t = make_ticket(conn, "DEMO-001")
    assert analyze(conn, settings, t["id"], DemoAdapter(30)) is True
    d = tickets.get_ticket_detail(conn, t["id"])
    a = d["current_attempt"]
    assert d["ticket"]["status"] == "READY_FOR_REVIEW"
    assert a["proposal"]["category"] == "AUTHENTICATION" and a["proposal"]["queue"] == "access-and-identity"
    assert "kb-auth-401" in a["proposal"]["source_ids"]
    assert any(s["id"] == "kb-auth-401" and s["cited"] and s["body"] for s in a["sources"])
    assert d["actions"]["approve"]
    tickets.approve(conn, t["id"], d["ticket"]["version"], a["id"])
    dec = conn.execute("SELECT * FROM decisions WHERE ticket_id = %s", (t["id"],)).fetchone()
    assert dec["action"] == "APPROVE" and dec["edited"] is False
    assert dec["final_text"] == a["proposal"]["draft_reply"]
    assert status(conn, t["id"]) == "RESOLVED"


def test_needs_info_then_edit_and_approve(conn, settings):
    t = make_ticket(conn, "DEMO-002")
    analyze(conn, settings, t["id"], DemoAdapter(30))
    d = tickets.get_ticket_detail(conn, t["id"])
    p = d["current_attempt"]["proposal"]
    assert p["disposition"] == "NEEDS_INFO" and "request_id" in p["requested_details"]
    edited = p["draft_reply"] + "\n\nP.S. A screenshot of the error is also fine."
    tickets.edit_approve(conn, t["id"], d["ticket"]["version"], d["current_attempt"]["id"], edited)
    dec = conn.execute("SELECT * FROM decisions WHERE ticket_id = %s", (t["id"],)).fetchone()
    assert dec["edited"] is True and dec["final_text"] == edited and dec["disposition"] == "NEEDS_INFO"


def test_no_kb_match_rule_skips_the_model(conn, settings):
    t = tickets.create_ticket(conn, "Office party", "Can we order pizza for the office party on Friday evening?")
    ad = ScriptedAdapter(AssertionError("model must not be called"))
    analyze(conn, settings, t["id"], ad)
    a = current_attempt(conn, t["id"])
    assert ad.calls == 0
    assert a["decided_by"] == "rule" and a["outcome"] == "MANUAL_REVIEW"
    assert status(conn, t["id"]) == "MANUAL_REVIEW"


def test_out_of_scope_manual_review_by_model(conn, settings):
    t = make_ticket(conn, "DEMO-006")
    analyze(conn, settings, t["id"], DemoAdapter(30))
    a = current_attempt(conn, t["id"])
    assert a["decided_by"] == "model" and a["outcome"] == "MANUAL_REVIEW"
    assert a["proposal"]["draft_reply"] is None
    d = tickets.get_ticket_detail(conn, t["id"])
    assert not d["actions"]["approve"] and d["actions"]["edit_approve"]


def test_unknown_source_is_rejected_and_cannot_be_approved(conn, settings):
    t = make_ticket(conn, "DEMO-004")
    analyze(conn, settings, t["id"], DemoAdapter(30))
    d = tickets.get_ticket_detail(conn, t["id"])
    a = d["current_attempt"]
    assert d["ticket"]["status"] == "ANALYSIS_FAILED"
    assert a["error_type"] == "CONTRACT_VIOLATION"
    assert any(e["code"] == "UNKNOWN_SOURCE" for e in a["validation_errors"])
    assert "kb-rate-limits-advanced" in a["invalid_citations"]
    assert not d["actions"]["approve"]
    with pytest.raises(ConflictError):
        tickets.approve(conn, t["id"], d["ticket"]["version"], a["id"])
    # The operator can still write a reply or route it.
    tickets.route_manual(conn, t["id"], d["ticket"]["version"], "invented citation")
    assert status(conn, t["id"]) == "MANUAL_REVIEW"


@pytest.mark.parametrize("raw,etype", [("not json at all", "INVALID_JSON"),
                                       ({"disposition": "DRAFT_REPLY"}, "CONTRACT_VIOLATION")])
def test_invalid_model_output_keeps_ticket(conn, settings, raw, etype):
    t = make_ticket(conn)
    analyze(conn, settings, t["id"], ScriptedAdapter(raw))
    a = current_attempt(conn, t["id"])
    assert a["state"] == "FAILED" and a["error_type"] == etype and a["raw_output"]
    assert status(conn, t["id"]) == "ANALYSIS_FAILED"


def test_timeout_then_retry_succeeds_and_history_is_kept(conn, settings):
    t = make_ticket(conn)
    ad = ScriptedAdapter(TIMEOUT, draft())
    analyze(conn, settings, t["id"], ad)
    assert status(conn, t["id"]) == "ANALYSIS_FAILED"
    assert current_attempt(conn, t["id"])["error_type"] == "TIMEOUT"
    analyze(conn, settings, t["id"], ad)
    assert status(conn, t["id"]) == "READY_FOR_REVIEW"
    d = tickets.get_ticket_detail(conn, t["id"])
    assert [x["state"] for x in d["attempts"]] == ["SUCCEEDED", "FAILED"]
    assert [e["action"] for e in d["events"]].count("ANALYSIS_STARTED") == 2


def test_adapter_crash_is_stored_as_internal_error(conn, settings):
    t = make_ticket(conn)
    analyze(conn, settings, t["id"], ScriptedAdapter(RuntimeError("bug")))
    assert current_attempt(conn, t["id"])["error_type"] == "INTERNAL_ERROR"


def test_retry_cannot_overwrite_resolved_ticket(conn, settings):
    t = make_ticket(conn)
    analyze(conn, settings, t["id"], ScriptedAdapter(draft()))
    a = current_attempt(conn, t["id"])
    tickets.approve(conn, t["id"], version(conn, t["id"]), a["id"])
    with pytest.raises(ConflictError) as e:
        analyze(conn, settings, t["id"], ScriptedAdapter(draft(draft_reply="different")))
    assert e.value.code == "ALREADY_RESOLVED"
    assert conn.execute("SELECT count(*) AS n FROM analysis_attempts WHERE ticket_id = %s", (t["id"],)).fetchone()["n"] == 1


def test_no_transaction_is_open_during_model_call(conn, settings, test_db_url):
    seen = {}

    def probe(_request):
        with psycopg.connect(test_db_url) as c:
            seen["open_tx"] = c.execute(
                """SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()
                   AND state LIKE 'idle in transaction%%' AND pid <> pg_backend_pid()"""
            ).fetchone()[0]
            # The ticket row must not be locked while the model runs.
            c.execute("SET lock_timeout = '500ms'")
            c.execute("SELECT 1 FROM tickets WHERE id = %s FOR UPDATE", (t["id"],))

    t = make_ticket(conn)
    conn.commit()
    analyze(conn, settings, t["id"], ScriptedAdapter(draft(), before=probe))
    assert seen["open_tx"] == 0


# --- stale results, stale actions, double clicks ---------------------------------------------


def _start(conn, settings, ticket_id, adapter_model="x"):
    return tickets.start_attempt(
        conn, ticket_id, version(conn, ticket_id), mode="demo", model=adapter_model,
        deadline_seconds=settings.llm_timeout_seconds + settings.attempt_grace_seconds,
        retrieve=triage.retriever(conn, settings),
    )


def _ok_result():
    p = {"disposition": "DRAFT_REPLY", "category": "AUTHENTICATION", "queue": "access-and-identity",
         "draft_reply": "late draft", "source_ids": ["kb-auth-401"], "requested_details": [], "reason": "r"}
    return AttemptResult(state="SUCCEEDED", model="x", decided_by="model", outcome="DRAFT_REPLY", proposal=p)


def test_late_result_does_not_override_operator_decision(conn, settings):
    t = make_ticket(conn)
    a, _ = _start(conn, settings, t["id"])
    tickets.route_manual(conn, t["id"], version(conn, t["id"]), "customer called, handling by phone")
    v = version(conn, t["id"])
    assert tickets.finish_attempt(conn, a["id"], _ok_result()) is False
    assert status(conn, t["id"]) == "MANUAL_REVIEW" and version(conn, t["id"]) == v
    row = conn.execute("SELECT state, late_result FROM analysis_attempts WHERE id = %s", (a["id"],)).fetchone()
    assert row["state"] == "SUPERSEDED" and row["late_result"]["outcome"] == "DRAFT_REPLY"


def test_late_result_of_old_attempt_does_not_replace_newer_attempt(conn, settings):
    t = make_ticket(conn)
    old, _ = _start(conn, settings, t["id"])
    # The old attempt hangs past its deadline and is expired; a new attempt starts and finishes.
    future = datetime.now(timezone.utc) + timedelta(seconds=settings.llm_timeout_seconds + settings.attempt_grace_seconds + 1)
    assert tickets.expire_stale_attempts(conn, now=future) == 1
    assert status(conn, t["id"]) == "ANALYSIS_FAILED"
    new, _ = _start(conn, settings, t["id"])
    new_result = _ok_result()
    new_result.proposal = dict(new_result.proposal, draft_reply="fresh draft")
    assert tickets.finish_attempt(conn, new["id"], new_result) is True
    assert tickets.finish_attempt(conn, old["id"], _ok_result()) is False
    cur = current_attempt(conn, t["id"])
    assert cur["id"] == new["id"] and cur["proposal"]["draft_reply"] == "fresh draft"


def test_late_result_while_newer_attempt_running(conn, settings):
    """Real threads: attempt 1 blocks in the model, operator routes to manual, attempt 2 runs, then 1 returns."""
    t = make_ticket(conn)
    slow = BlockingAdapter(draft(draft_reply="stale draft"))
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("applied", triage.run_analysis(t["id"], 1, settings, slow)))
    th.start()
    assert slow.entered.wait(5)
    with db.connection() as c2:
        tickets.route_manual(c2, t["id"], version(c2, t["id"]), "taking over")
        triage.run_analysis(t["id"], version(c2, t["id"]), settings, ScriptedAdapter(draft(draft_reply="fresh draft")))
    slow.release.set()
    th.join(10)
    assert out["applied"] is False
    assert current_attempt(conn, t["id"])["proposal"]["draft_reply"] == "fresh draft"
    actions = [e["action"] for e in tickets.get_ticket_detail(conn, t["id"])["events"]]
    assert "LATE_RESULT_IGNORED" in actions


def test_hung_attempt_expires_and_can_be_retried(conn, settings):
    t = make_ticket(conn)
    _start(conn, settings, t["id"])  # process "crashes": the attempt is never finished
    assert status(conn, t["id"]) == "ANALYZING"
    with pytest.raises(ConflictError) as e:
        analyze(conn, settings, t["id"], ScriptedAdapter(draft()))
    assert e.value.code == "ANALYSIS_IN_PROGRESS"
    # Manual review stays available while it hangs:
    d = tickets.get_ticket_detail(conn, t["id"])
    assert d["actions"]["route_manual"]
    future = datetime.now(timezone.utc) + timedelta(minutes=5)
    assert tickets.expire_stale_attempts(conn, now=future) == 1
    assert status(conn, t["id"]) == "ANALYSIS_FAILED"
    assert analyze(conn, settings, t["id"], ScriptedAdapter(draft())) is True
    assert status(conn, t["id"]) == "READY_FOR_REVIEW"


def test_stale_version_gets_conflict(conn, settings):
    t = make_ticket(conn)
    analyze(conn, settings, t["id"], ScriptedAdapter(draft(draft_reply="first")))
    seen_version = version(conn, t["id"])
    seen_attempt = current_attempt(conn, t["id"])["id"]
    analyze(conn, settings, t["id"], ScriptedAdapter(draft(draft_reply="second")))  # another operator re-ran
    with pytest.raises(ConflictError) as e:
        tickets.approve(conn, t["id"], seen_version, seen_attempt)
    assert e.value.code == "STALE_VERSION"
    with pytest.raises(ConflictError):
        tickets.edit_approve(conn, t["id"], seen_version, seen_attempt, "my text")
    with pytest.raises(ConflictError):
        tickets.route_manual(conn, t["id"], seen_version)
    assert conn.execute("SELECT count(*) AS n FROM decisions").fetchone()["n"] == 0


def test_double_approve_concurrent_creates_one_decision(conn, settings):
    t = make_ticket(conn)
    analyze(conn, settings, t["id"], ScriptedAdapter(draft()))
    v, aid = version(conn, t["id"]), current_attempt(conn, t["id"])["id"]
    barrier = threading.Barrier(2)
    results = []

    def click():
        with db.connection() as c:
            barrier.wait()
            try:
                tickets.approve(c, t["id"], v, aid)
                results.append("ok")
            except ConflictError as e:
                results.append(e.code)

    threads = [threading.Thread(target=click) for _ in range(2)]
    [th.start() for th in threads]
    [th.join(10) for th in threads]
    assert sorted(results) == ["ALREADY_RESOLVED", "ok"]
    assert conn.execute("SELECT count(*) AS n FROM decisions WHERE ticket_id = %s", (t["id"],)).fetchone()["n"] == 1
    approvals = conn.execute(
        "SELECT count(*) AS n FROM ticket_events WHERE ticket_id = %s AND action = 'APPROVED'", (t["id"],)
    ).fetchone()["n"]
    assert approvals == 1


def test_decision_table_rejects_second_row(conn, settings):
    t = make_ticket(conn)
    conn.execute("INSERT INTO decisions (ticket_id, action, final_text, edited, ticket_version) VALUES (%s,'APPROVE','x',false,1)", (t["id"],))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO decisions (ticket_id, action, final_text, edited, ticket_version) VALUES (%s,'APPROVE','y',false,1)", (t["id"],))
    conn.rollback()


def test_data_survives_pool_restart(conn, settings, test_db_url):
    t = make_ticket(conn)
    analyze(conn, settings, t["id"], ScriptedAdapter(draft()))
    tickets.approve(conn, t["id"], version(conn, t["id"]), current_attempt(conn, t["id"])["id"])
    conn.commit()
    db.close_pool()
    db.init_pool(test_db_url)
    with db.connection() as c:
        d = tickets.get_ticket_detail(c, t["id"])
    assert d["ticket"]["status"] == "RESOLVED" and d["decision"]["action"] == "APPROVE"
    assert [e["action"] for e in d["events"]][-1] == "APPROVED"
