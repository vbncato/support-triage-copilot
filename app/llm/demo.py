"""Demo mode: replays recorded, synthetic model responses. No network, no API key.

Recordings are written by hand for specific demo tickets (fixtures/model-demo/recordings.json) and are
looked up by a hash of the normalised ticket text, so a ticket re-created with the same text (for
example by scripts/demo.ps1) gets the same recording. Unknown tickets get NO_DEMO_RECORDING: demo mode
never invents a draft.

Each recording is a list of steps; attempt N uses step N (the last step repeats). A step is one of:
  {"output": {...}}             -> returned as the model's JSON text
  {"raw": "..."}                -> returned verbatim (to exercise INVALID_JSON)
  {"error": "TIMEOUT", ...}     -> raised as that error type
  optional "delay_seconds": n   -> simulated latency; if it exceeds the timeout, TIMEOUT is raised
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from app.llm.base import LLMError, ModelOutput, ModelRequest

ROOT = Path(__file__).resolve().parent.parent.parent
RECORDINGS_FILE = ROOT / "fixtures" / "model-demo" / "recordings.json"
DEMO_TICKETS_FILE = ROOT / "fixtures" / "tickets" / "demo-tickets.json"


def content_key(subject: str, body: str) -> str:
    norm = re.sub(r"\s+", " ", f"{subject}\n{body}".strip().lower())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def load_recordings() -> dict[str, dict]:
    tickets = {t["external_id"]: t for t in json.loads(DEMO_TICKETS_FILE.read_text("utf-8"))}
    recordings = json.loads(RECORDINGS_FILE.read_text("utf-8"))
    by_key: dict[str, dict] = {}
    for rec in recordings:
        t = tickets[rec["ticket"]]
        by_key[content_key(t["subject"], t["body"])] = rec
    return by_key


class DemoAdapter:
    mode = "demo"
    model = "demo-recordings-v1"

    def __init__(self, timeout_seconds: float, recordings: dict[str, dict] | None = None):
        self.timeout_seconds = timeout_seconds
        self.recordings = recordings if recordings is not None else load_recordings()

    def complete(self, request: ModelRequest) -> ModelOutput:
        rec = self.recordings.get(content_key(request.subject, request.body))
        if rec is None:
            raise LLMError(
                "NO_DEMO_RECORDING",
                "No recorded demo response matches this ticket text. Use live mode or manual review.",
            )
        steps = rec["steps"]
        step = steps[min(request.attempt_no, len(steps)) - 1]
        delay = float(step.get("delay_seconds", 0))
        if delay > self.timeout_seconds:
            time.sleep(self.timeout_seconds)
            raise LLMError("TIMEOUT", f"Simulated: no answer within {self.timeout_seconds:g}s (demo)")
        if delay:
            time.sleep(delay)
        if "error" in step:
            raise LLMError(step["error"], step.get("detail", "Simulated provider failure (demo)"))
        if "raw" in step:
            return ModelOutput(step["raw"], self.model, "end_turn")
        return ModelOutput(json.dumps(step["output"]), self.model, "end_turn")
