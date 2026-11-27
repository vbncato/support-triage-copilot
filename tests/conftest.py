"""Shared fixtures.

Integration tests use a separate database (TEST_DATABASE_URL, default triage_test on the Compose
PostgreSQL). It is created if missing and its schema is rebuilt once per session, so running the
tests never touches the demo data in the main database.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import psycopg
import pytest

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "")

if TEST_DB_URL:
    # The app reads DATABASE_URL through get_settings(); point it at the test database.
    os.environ["DATABASE_URL"] = TEST_DB_URL
    os.environ["SEED_DEMO_TICKETS"] = "false"
    os.environ["MODEL_MODE"] = "demo"
    os.environ["LLM_API_KEY"] = ""


def _ensure_database(url: str) -> None:
    name = urlparse(url).path.lstrip("/")
    admin = url.rsplit("/", 1)[0] + "/postgres"
    with psycopg.connect(admin, autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            c.execute(f'CREATE DATABASE "{name}"')


@pytest.fixture(scope="session")
def test_db_url():
    if not TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL not set (run tests inside Compose, see README)")
    _ensure_database(TEST_DB_URL)
    with psycopg.connect(TEST_DB_URL, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    from app import db

    db.init_pool(TEST_DB_URL)
    with db.connection() as conn:
        db.apply_schema(conn)
        db.seed_knowledge(conn)
    yield TEST_DB_URL
    db.close_pool()


@pytest.fixture()
def conn(test_db_url):
    from app import db

    with db.connection() as c:
        c.execute("TRUNCATE tickets, analysis_attempts, ticket_events, decisions RESTART IDENTITY CASCADE")
        yield c


@pytest.fixture()
def settings(test_db_url):
    from app.config import get_settings

    get_settings.cache_clear()
    return get_settings()
