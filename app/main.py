"""FastAPI application: REST API, operator UI and startup wiring."""

from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app import db, knowledge, tickets, triage
from app.config import (
    BODY_MAX,
    CATEGORY_QUEUE,
    EXTERNAL_ID_MAX,
    FINAL_TEXT_MAX,
    SUBJECT_MAX,
    get_settings,
)
from app.llm.base import ERROR_TYPES
from app.llm.provider import build_adapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("app")

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))  # autoescape is on for .html
# Cache-busting token for /static links: changes whenever CSS or JS changes.
ASSET_VERSION = hashlib.sha256(
    b"".join((HERE / "static" / f).read_bytes() for f in ("app.css", "app.js"))
).hexdigest()[:10]


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    db.init_pool(settings.database_url)
    with db.connection() as conn:
        db.apply_schema(conn)
        n_kb = db.seed_knowledge(conn)
        n_demo = db.seed_demo_tickets(conn) if settings.seed_demo_tickets else 0
        expired = tickets.expire_stale_attempts(conn)
    log.info(
        "startup mode=%s model=%s kb_sections=%d demo_tickets_created=%d expired_attempts=%d",
        settings.model_mode, settings.model_label, n_kb, n_demo, expired,
    )
    yield
    db.close_pool()


app = FastAPI(
    title="Support Triage Copilot",
    version="1.0.0",
    description=(
        "Triage assistant for a synthetic B2B API support desk. The model proposes; the operator decides. "
        "Error bodies have the shape {\"error\": {\"code\", \"message\", ...}}. "
        "409 means the ticket changed since the caller loaded it (reload and retry)."
    ),
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


# --- errors -----------------------------------------------------------------------------------


def _error(http_status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=http_status, content=jsonable_encoder({"error": {"code": code, "message": message, **extra}}))


@app.exception_handler(tickets.ConflictError)
async def _conflict(_: Request, exc: tickets.ConflictError):
    return _error(409, exc.code, exc.message, **exc.extra)


@app.exception_handler(tickets.InvalidInput)
async def _invalid(_: Request, exc: tickets.InvalidInput):
    return _error(422, exc.code, exc.message)


@app.exception_handler(tickets.NotFoundError)
async def _not_found(_: Request, exc: tickets.NotFoundError):
    return _error(404, "NOT_FOUND", f"Ticket {exc.args[0]} not found")


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


ERRORS = {
    404: {"model": ErrorResponse, "description": "Not found"},
    409: {"model": ErrorResponse, "description": "Stale version, analysis in progress, or already resolved"},
    422: {"model": ErrorResponse, "description": "Invalid input"},
}


# --- API models -------------------------------------------------------------------------------


class TicketIn(BaseModel):
    subject: str = Field(..., max_length=SUBJECT_MAX, examples=["401 after token refresh"])
    body: str = Field(..., max_length=BODY_MAX)
    external_id: str | None = Field(None, max_length=EXTERNAL_ID_MAX, description="Optional ID from a fixture or upstream system")


class AnalysisIn(BaseModel):
    expected_version: int = Field(..., description="Ticket version the caller saw")


class DecisionIn(BaseModel):
    action: Literal["approve", "edit_approve", "route_manual"]
    expected_version: int = Field(..., description="Ticket version the operator saw")
    attempt_id: int | None = Field(None, description="Analysis attempt whose draft the operator saw (required for approve)")
    final_text: str | None = Field(None, max_length=FINAL_TEXT_MAX, description="Required for edit_approve")
    note: str | None = Field(None, max_length=500, description="Optional note for route_manual")


# --- API --------------------------------------------------------------------------------------


@app.get("/healthz", tags=["system"])
def healthz():
    s = get_settings()
    with db.connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok", "database": "ok", "mode": s.model_mode, "model": s.model_label}


@app.post("/api/tickets", status_code=201, tags=["tickets"], responses=ERRORS)
def create_ticket(payload: TicketIn):
    with db.connection() as conn:
        t = tickets.create_ticket(conn, payload.subject, payload.body, payload.external_id)
    return t


@app.get("/api/tickets", tags=["tickets"])
def list_tickets(
    status: Literal["NEW", "ANALYZING", "READY_FOR_REVIEW", "MANUAL_REVIEW", "ANALYSIS_FAILED", "RESOLVED"]
    | None = Query(None, description="Filter by status"),
):
    with db.connection() as conn:
        tickets.expire_stale_attempts(conn)
        return tickets.list_tickets(conn, status)


@app.get("/api/tickets/{ticket_id}", tags=["tickets"], responses=ERRORS)
def get_ticket(ticket_id: int):
    with db.connection() as conn:
        return tickets.get_ticket_detail(conn, ticket_id)


@app.post("/api/tickets/{ticket_id}/analysis", tags=["tickets"], responses=ERRORS)
def analyze_ticket(ticket_id: int, payload: AnalysisIn):
    """Start (or retry) analysis and wait for it, bounded by LLM_TIMEOUT_SECONDS.

    Model errors do not fail the request: the attempt is stored with its error type and the ticket
    moves to ANALYSIS_FAILED. A RESOLVED ticket cannot be re-analysed (409).
    """
    settings = get_settings()
    applied = triage.run_analysis(ticket_id, payload.expected_version, settings, build_adapter(settings))
    with db.connection() as conn:
        detail = tickets.get_ticket_detail(conn, ticket_id)
    detail["result_applied"] = applied
    return detail


@app.post("/api/tickets/{ticket_id}/decisions", tags=["tickets"], responses=ERRORS)
def decide(ticket_id: int, payload: DecisionIn):
    with db.connection() as conn:
        if payload.action == "approve":
            tickets.approve(conn, ticket_id, payload.expected_version, payload.attempt_id)
        elif payload.action == "edit_approve":
            tickets.edit_approve(conn, ticket_id, payload.expected_version, payload.attempt_id, payload.final_text or "")
        else:
            tickets.route_manual(conn, ticket_id, payload.expected_version, payload.note)
        return tickets.get_ticket_detail(conn, ticket_id)


@app.get("/api/kb", tags=["knowledge"])
def list_kb():
    with db.connection() as conn:
        return knowledge.list_sections(conn)


@app.get("/api/kb/{section_id}", tags=["knowledge"], responses=ERRORS)
def get_kb(section_id: str):
    with db.connection() as conn:
        s = knowledge.get_section(conn, section_id)
    if s is None:
        return _error(404, "NOT_FOUND", f"Knowledge-base section {section_id!r} not found")
    return s


# --- UI ---------------------------------------------------------------------------------------


def _ctx(request: Request, **kw):
    s = get_settings()
    return {"request": request, "mode": s.model_mode, "model": s.model_label, "asset_version": ASSET_VERSION, **kw}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def ui_queue(request: Request, status: str | None = None):
    if status not in tickets.STATUSES:
        status = None
    with db.connection() as conn:
        tickets.expire_stale_attempts(conn)
        rows = tickets.list_tickets(conn, status)
    return templates.TemplateResponse(
        request, "queue.html", _ctx(request, rows=rows, statuses=tickets.STATUSES, status=status)
    )


@app.get("/tickets/{ticket_id}", response_class=HTMLResponse, include_in_schema=False)
def ui_ticket(request: Request, ticket_id: int):
    with db.connection() as conn:
        try:
            d = tickets.get_ticket_detail(conn, ticket_id)
        except tickets.NotFoundError:
            return HTMLResponse("Ticket not found", status_code=404)
    return templates.TemplateResponse(
        request, "ticket.html",
        _ctx(request, d=d, error_types=ERROR_TYPES, category_queue=CATEGORY_QUEUE),
    )


@app.get("/kb/{section_id}", response_class=HTMLResponse, include_in_schema=False)
def ui_kb(request: Request, section_id: str):
    with db.connection() as conn:
        s = knowledge.get_section(conn, section_id)
    if s is None:
        return HTMLResponse("Section not found", status_code=404)
    return templates.TemplateResponse(request, "kb.html", _ctx(request, s=s))
