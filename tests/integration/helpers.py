import json
import threading
from pathlib import Path

from app import tickets
from app.llm.base import LLMError, ModelOutput

ROOT = Path(__file__).resolve().parents[2]
DEMO = {t["external_id"]: t for t in json.loads((ROOT / "fixtures/tickets/demo-tickets.json").read_text("utf-8"))}


def make_ticket(conn, key="DEMO-001", external_id=None):
    t = DEMO[key]
    return tickets.create_ticket(conn, t["subject"], t["body"], external_id)


def version(conn, ticket_id):
    return conn.execute("SELECT version FROM tickets WHERE id = %s", (ticket_id,)).fetchone()["version"]


def status(conn, ticket_id):
    return conn.execute("SELECT status FROM tickets WHERE id = %s", (ticket_id,)).fetchone()["status"]


class ScriptedAdapter:
    """Returns the given outputs in order; an Exception instance is raised instead."""

    mode = "demo"
    model = "scripted-test-adapter"

    def __init__(self, *outputs, before=None):
        self.outputs = list(outputs)
        self.calls = 0
        self.before = before

    def complete(self, request):
        self.calls += 1
        if self.before:
            self.before(request)
        item = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
        if isinstance(item, Exception):
            raise item
        return ModelOutput(item if isinstance(item, str) else json.dumps(item), self.model, "end_turn")


class BlockingAdapter:
    """Blocks inside complete() until released, to simulate a slow or late model."""

    mode = "demo"
    model = "blocking-test-adapter"

    def __init__(self, output):
        self.output = output
        self.entered = threading.Event()
        self.release = threading.Event()

    def complete(self, request):
        self.entered.set()
        self.release.wait(10)
        return ModelOutput(json.dumps(self.output), self.model, "end_turn")


def draft(source="kb-auth-401", **kw):
    d = {
        "disposition": "DRAFT_REPLY", "category": "AUTHENTICATION",
        "draft_reply": "Refresh the token about 5 minutes before it expires.",
        "source_ids": [source], "requested_details": [], "reason": "token_expired",
    }
    d.update(kw)
    return d


TIMEOUT = LLMError("TIMEOUT", "simulated in test")
