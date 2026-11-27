"""Database connections, schema bootstrap and fixture seeding."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_FILE = ROOT / "db" / "init" / "001_schema.sql"
KB_DIR = ROOT / "fixtures" / "knowledge"
DEMO_TICKETS_FILE = ROOT / "fixtures" / "tickets" / "demo-tickets.json"

_pool: ConnectionPool | None = None
_pool_url: str | None = None


def init_pool(database_url: str) -> ConnectionPool:
    global _pool, _pool_url
    if _pool is not None and _pool_url == database_url:
        return _pool
    close_pool()
    _pool = ConnectionPool(
        database_url,
        min_size=1,
        max_size=10,
        # autocommit: plain reads never leave an implicit transaction open, and every
        # `with conn.transaction()` block is a real top-level transaction (not a savepoint).
        kwargs={"row_factory": dict_row, "autocommit": True},
        open=True,
    )
    _pool.wait(timeout=30)
    _pool_url = database_url
    return _pool


def close_pool() -> None:
    global _pool, _pool_url
    if _pool is not None:
        _pool.close()
    _pool = None
    _pool_url = None


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """A pooled connection. Each `with conn.transaction()` block is one atomic unit."""
    if _pool is None:
        raise RuntimeError("Database pool is not initialised")
    with _pool.connection() as conn:
        yield conn


def apply_schema(conn: psycopg.Connection) -> None:
    with conn.transaction():
        # Serialise concurrent bootstraps (app start + test session) on one advisory lock.
        conn.execute("SELECT pg_advisory_xact_lock(7314001)")
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))


def parse_kb_file(path: Path) -> dict:
    """Markdown with a small header block: `id:`, `title:`, `tags:` lines, then `---`, then body."""
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    header, sep, body = text.partition("\n---\n")
    if not sep:
        raise ValueError(f"{path.name}: missing '---' separator")
    meta: dict[str, str] = {}
    for line in header.strip().splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    tags = [t.strip() for t in meta.get("tags", "").split(",") if t.strip()]
    return {"id": meta["id"], "title": meta["title"], "tags": tags, "body": body.strip()}


def load_kb_fixtures() -> list[dict]:
    sections = [parse_kb_file(p) for p in sorted(KB_DIR.glob("*.md"))]
    ids = [s["id"] for s in sections]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate knowledge-base IDs")
    return sections


def seed_knowledge(conn: psycopg.Connection) -> int:
    """Upsert the KB from fixtures. Sections removed from fixtures are deleted."""
    import hashlib

    sections = load_kb_fixtures()
    with conn.transaction():
        for s in sections:
            digest = hashlib.sha256(
                json.dumps(s, sort_keys=True).encode("utf-8")
            ).hexdigest()
            conn.execute(
                """
                INSERT INTO kb_sections (id, title, body, tags, tags_text, content_hash)
                VALUES (%(id)s, %(title)s, %(body)s, %(tags)s, %(tags_text)s, %(hash)s)
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title, body = EXCLUDED.body, tags = EXCLUDED.tags,
                    tags_text = EXCLUDED.tags_text, content_hash = EXCLUDED.content_hash,
                    updated_at = now()
                WHERE kb_sections.content_hash <> EXCLUDED.content_hash
                """,
                {**s, "tags_text": " ".join(s["tags"]), "hash": digest},
            )
        conn.execute(
            "DELETE FROM kb_sections WHERE NOT (id = ANY(%s))", ([s["id"] for s in sections],)
        )
    return len(sections)


def seed_demo_tickets(conn: psycopg.Connection) -> int:
    """Insert demo tickets once, keyed by external_id. Existing tickets are never touched."""
    tickets = json.loads(DEMO_TICKETS_FILE.read_text(encoding="utf-8"))
    created = 0
    with conn.transaction():
        for t in tickets:
            row = conn.execute(
                """
                INSERT INTO tickets (external_id, subject, body, status)
                VALUES (%s, %s, %s, 'NEW')
                ON CONFLICT (external_id) DO NOTHING
                RETURNING id
                """,
                (t["external_id"], t["subject"], t["body"]),
            ).fetchone()
            if row:
                created += 1
                conn.execute(
                    """INSERT INTO ticket_events (ticket_id, actor, action, to_status, detail)
                       VALUES (%s, 'system', 'TICKET_CREATED', 'NEW', %s)""",
                    (row["id"], json.dumps({"source": "demo-seed"})),
                )
    return created
