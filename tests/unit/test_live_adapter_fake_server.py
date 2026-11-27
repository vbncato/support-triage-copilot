"""Live adapter against a local fake HTTP server (substituted provider).

This runs the real `anthropic` SDK code path (request building, HTTP, timeouts, error classes)
without contacting Anthropic. It verifies how failures are classified, not model quality.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.config import load_settings
from app.llm.base import LLMError, ModelRequest
from app.llm.provider import AnthropicAdapter, UnavailableAdapter, build_adapter

FAKE_KEY = "sk-ant-api03-FAKE-not-a-real-key-000"  # real key shape, not a real key
STATE = {"mode": "ok", "last_body": None, "last_headers": None}


def _message(text: str, stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_fake", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}], "stop_reason": stop_reason,
        "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 10},
    }


VALID = json.dumps({
    "disposition": "MANUAL_REVIEW", "category": "OTHER", "draft_reply": None,
    "source_ids": [], "requested_details": [], "reason": "fake",
})


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("content-length", 0))
        STATE["last_body"] = json.loads(self.rfile.read(n))
        STATE["last_headers"] = {k.lower(): v for k, v in self.headers.items()}
        mode = STATE["mode"]
        if mode == "slow":
            time.sleep(2)
        if mode == "500":
            return self._send(500, {"type": "error", "error": {"type": "api_error", "message": "boom"}})
        if mode == "429":
            return self._send(429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}})
        if mode == "refusal":
            return self._send(200, _message("", "refusal"))
        if mode == "max_tokens":
            return self._send(200, _message('{"disposition": "DRA', "max_tokens"))
        if mode == "bad_json":
            return self._send(200, _message("Sure! Here is my answer."))
        return self._send(200, _message(VALID))

    def _send(self, code, payload):
        data = json.dumps(payload).encode()
        try:
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("request-id", "req_fake_1")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client gave up (timeout test)


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture()
def adapter(server, monkeypatch):
    monkeypatch.setenv("MODEL_MODE", "live")
    monkeypatch.setenv("LLM_API_KEY", FAKE_KEY)
    monkeypatch.setenv("LLM_BASE_URL", server)
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "0.5")
    return AnthropicAdapter(load_settings())


REQ = ModelRequest("s", "b", "system text", "user text", 1)


def run(adapter, mode):
    STATE["mode"] = mode
    return adapter.complete(REQ)


def test_ok_request_shape(adapter):
    out = run(adapter, "ok")
    assert json.loads(out.text)["disposition"] == "MANUAL_REVIEW"
    body = STATE["last_body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["effort"] == "low"
    assert body["system"] == "system text"
    assert "thinking" not in body and "temperature" not in body
    h = STATE["last_headers"]
    # Observed with anthropic==2.14.0 against this fake server: the key is sent as
    # "Authorization: Bearer <key>". Accept x-api-key too in case the SDK changes; either way, our key only.
    # Whether the real API accepts this header for a real key is verified only by a live run.
    assert h.get("x-api-key") == FAKE_KEY or h.get("authorization") == f"Bearer {FAKE_KEY}"


@pytest.mark.parametrize("mode,etype", [
    ("slow", "TIMEOUT"), ("500", "HTTP_ERROR"), ("429", "HTTP_ERROR"),
    ("refusal", "MODEL_REFUSAL"), ("max_tokens", "TRUNCATED"),
])
def test_failures_are_classified(adapter, mode, etype):
    t0 = time.monotonic()
    with pytest.raises(LLMError) as e:
        run(adapter, mode)
    assert e.value.error_type == etype
    assert FAKE_KEY not in e.value.detail
    assert time.monotonic() - t0 < 1.9  # bounded by the 0.5 s timeout, no hidden retries


def test_bad_json_passes_through_to_validator(adapter):
    from app.llm.contract import validate_output

    out = run(adapter, "bad_json")
    assert validate_output(out.text, []).errors[0]["code"] == "INVALID_JSON"


def test_connection_error(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", FAKE_KEY)
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:9")  # nothing listens here
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "1")
    with pytest.raises(LLMError) as e:
        AnthropicAdapter(load_settings()).complete(REQ)
    assert e.value.error_type in ("CONNECTION_ERROR", "TIMEOUT")


def test_live_without_key_is_a_stored_config_error(monkeypatch):
    monkeypatch.setenv("MODEL_MODE", "live")
    monkeypatch.setenv("LLM_API_KEY", "")
    ad = build_adapter(replace(load_settings(), model_mode="live"))
    assert isinstance(ad, UnavailableAdapter)
    with pytest.raises(LLMError) as e:
        ad.complete(REQ)
    assert e.value.error_type == "CONFIG_ERROR"
