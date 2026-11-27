"""HTTP contract and HTML rendering, via FastAPI TestClient on the real test database."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

from .helpers import DEMO

pytestmark = pytest.mark.db


@pytest.fixture()
def client(conn):
    # No `with`: the lifespan would close the shared test pool. The pool is opened by conftest.
    return TestClient(app)


def create(client, key="DEMO-001", **extra):
    t = DEMO[key]
    r = client.post("/api/tickets", json={"subject": t["subject"], "body": t["body"], **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_create_list_get(client):
    t = create(client, external_id="FIX-1")
    assert t["status"] == "NEW" and t["external_id"] == "FIX-1"
    assert any(x["id"] == t["id"] for x in client.get("/api/tickets").json())
    assert client.get("/api/tickets?status=NEW").status_code == 200
    d = client.get(f"/api/tickets/{t['id']}").json()
    assert d["ticket"]["subject"] == DEMO["DEMO-001"]["subject"] and d["current_attempt"] is None
    assert client.get("/api/tickets/99999").status_code == 404


def test_create_validation_errors(client):
    r = client.post("/api/tickets", json={"subject": "  ", "body": "x"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "EMPTY_SUBJECT"
    assert client.post("/api/tickets", json={"subject": "s"}).status_code == 422
    assert client.post("/api/tickets", json={"subject": "s", "body": "b" * 5001}).status_code == 422


def test_full_flow_over_http(client):
    t = create(client)
    d = client.post(f"/api/tickets/{t['id']}/analysis", json={"expected_version": 1}).json()
    assert d["ticket"]["status"] == "READY_FOR_REVIEW" and d["result_applied"] is True
    a = d["current_attempt"]
    body = {"action": "approve", "expected_version": d["ticket"]["version"], "attempt_id": a["id"]}
    r1 = client.post(f"/api/tickets/{t['id']}/decisions", json=body)
    r2 = client.post(f"/api/tickets/{t['id']}/decisions", json=body)  # double click
    assert r1.status_code == 200 and r1.json()["ticket"]["status"] == "RESOLVED"
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "ALREADY_RESOLVED"
    r3 = client.post(f"/api/tickets/{t['id']}/analysis", json={"expected_version": r1.json()["ticket"]["version"]})
    assert r3.status_code == 409


def test_stale_action_409_body(client):
    t = create(client, "DEMO-002")
    d = client.post(f"/api/tickets/{t['id']}/analysis", json={"expected_version": 1}).json()
    r = client.post(f"/api/tickets/{t['id']}/decisions",
                    json={"action": "route_manual", "expected_version": 1})
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "STALE_VERSION" and err["current_version"] == d["ticket"]["version"]
    assert "Reload" in err["message"]


def test_edit_approve_requires_text(client):
    t = create(client, "DEMO-002")
    d = client.post(f"/api/tickets/{t['id']}/analysis", json={"expected_version": 1}).json()
    r = client.post(f"/api/tickets/{t['id']}/decisions",
                    json={"action": "edit_approve", "expected_version": d["ticket"]["version"], "final_text": " "})
    assert r.status_code == 422 and r.json()["error"]["code"] == "EMPTY_FINAL_TEXT"


def test_knowledge_endpoints(client):
    assert len(client.get("/api/kb").json()) == 12
    s = client.get("/api/kb/kb-auth-401").json()
    assert s["title"].startswith("401")
    assert client.get("/api/kb/kb-nope").status_code == 404
    assert "401 Unauthorized" in client.get("/kb/kb-auth-401").text


def test_html_escapes_ticket_text(client):
    evil = '<script>alert("x")</script><img src=x onerror=alert(1)>'
    r = client.post("/api/tickets", json={"subject": "XSS " + evil, "body": "Body " + evil})
    tid = r.json()["id"]
    for page in ("/", f"/tickets/{tid}"):
        html = client.get(page).text
        assert "<script>alert" not in html and "<img src=x" not in html
        assert "&lt;script&gt;alert(" in html


def test_openapi_documents_endpoints(client):
    spec = client.get("/openapi.json").json()
    for p in ("/api/tickets", "/api/tickets/{ticket_id}", "/api/tickets/{ticket_id}/analysis",
              "/api/tickets/{ticket_id}/decisions", "/api/kb/{section_id}"):
        assert p in spec["paths"]
    assert "409" in spec["paths"]["/api/tickets/{ticket_id}/decisions"]["post"]["responses"]


def test_html_escapes_operator_final_text(client):
    t = create(client, "DEMO-003")
    d = client.post(f"/api/tickets/{t['id']}/analysis", json={"expected_version": 1}).json()
    evil = 'Reply <script>alert("x")</script>'
    r = client.post(f"/api/tickets/{t['id']}/decisions",
                    json={"action": "edit_approve", "expected_version": d["ticket"]["version"], "final_text": evil})
    assert r.status_code == 200
    html = client.get(f"/tickets/{t['id']}").text
    assert "<script>alert" not in html and "&lt;script&gt;alert(" in html
