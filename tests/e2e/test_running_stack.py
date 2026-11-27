"""End-to-end checks against a running Compose stack in demo mode, over real HTTP.

    docker compose run --rm -e E2E_BASE_URL=http://app:8000 app python -m pytest tests/e2e

Creates new tickets each run (no reset needed); the demo recordings match by ticket text.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import httpx
import pytest

BASE = os.environ.get("E2E_BASE_URL", "")
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(not BASE, reason="E2E_BASE_URL not set")]
DEMO = {t["external_id"]: t for t in json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures/tickets/demo-tickets.json").read_text("utf-8"))}


@pytest.fixture(scope="module")
def http():
    with httpx.Client(base_url=BASE, timeout=60) as c:
        assert c.get("/healthz").json()["mode"] == "demo", "e2e expects demo mode"
        yield c


def new(http, key):
    t = DEMO[key]
    r = http.post("/api/tickets", json={"subject": t["subject"], "body": t["body"],
                                        "external_id": f"E2E-{key}-{uuid.uuid4().hex[:8]}"})
    assert r.status_code == 201, r.text
    return r.json()


def analyze(http, t, version=None):
    v = version if version is not None else http.get(f"/api/tickets/{t['id']}").json()["ticket"]["version"]
    r = http.post(f"/api/tickets/{t['id']}/analysis", json={"expected_version": v})
    assert r.status_code == 200, r.text
    return r.json()


def decide(http, t, d, action, **kw):
    body = {"action": action, "expected_version": d["ticket"]["version"],
            "attempt_id": (d["current_attempt"] or {}).get("id"), **kw}
    return http.post(f"/api/tickets/{t['id']}/decisions", json=body)


def test_401_draft_with_openable_source_and_approve(http):
    t = new(http, "DEMO-001")
    d = analyze(http, t)
    p = d["current_attempt"]["proposal"]
    assert d["ticket"]["status"] == "READY_FOR_REVIEW" and p["category"] == "AUTHENTICATION"
    assert p["draft_reply"] and "kb-auth-401" in p["source_ids"]
    page = http.get("/kb/kb-auth-401")
    assert page.status_code == 200 and "token_expired" in page.text
    r = decide(http, t, d, "approve")
    assert r.status_code == 200 and r.json()["decision"]["action"] == "APPROVE"


def test_ambiguous_needs_info_edit_and_approve(http):
    t = new(http, "DEMO-002")
    d = analyze(http, t)
    p = d["current_attempt"]["proposal"]
    assert p["disposition"] == "NEEDS_INFO" and {"request_id", "timestamp"} <= set(p["requested_details"])
    r = decide(http, t, d, "edit_approve", final_text=p["draft_reply"] + "\nThanks for your patience.")
    assert r.status_code == 200 and r.json()["decision"]["edited"] is True


def test_out_of_scope_goes_to_manual_review(http):
    t = new(http, "DEMO-003")
    d = analyze(http, t)
    assert d["ticket"]["status"] == "MANUAL_REVIEW" and d["current_attempt"]["proposal"]["draft_reply"] is None


def test_invented_source_is_rejected(http):
    t = new(http, "DEMO-004")
    d = analyze(http, t)
    a = d["current_attempt"]
    assert d["ticket"]["status"] == "ANALYSIS_FAILED" and a["error_type"] == "CONTRACT_VIOLATION"
    assert "kb-rate-limits-advanced" in a["invalid_citations"] and not d["actions"]["approve"]
    assert decide(http, t, d, "approve").status_code == 409


def test_timeout_keeps_ticket_and_retry_recovers(http):
    t = new(http, "DEMO-005")
    d = analyze(http, t)
    assert d["ticket"]["status"] == "ANALYSIS_FAILED" and d["current_attempt"]["error_type"] == "TIMEOUT"
    assert d["actions"]["route_manual"] and d["actions"]["edit_approve"]
    d2 = analyze(http, t, d["ticket"]["version"])
    assert d2["ticket"]["status"] == "READY_FOR_REVIEW" and d2["current_attempt"]["attempt_no"] == 2


def test_stale_action_and_double_approve(http):
    t = new(http, "DEMO-008")
    d1 = analyze(http, t)
    d2 = analyze(http, t, d1["ticket"]["version"])  # someone re-ran the analysis
    stale = decide(http, t, d1, "approve")
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "STALE_VERSION"
    ok = decide(http, t, d2, "approve")
    again = decide(http, t, d2, "approve")
    assert ok.status_code == 200 and again.status_code == 409
    events = http.get(f"/api/tickets/{t['id']}").json()["events"]
    assert [e["action"] for e in events].count("APPROVED") == 1
