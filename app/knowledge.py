"""Knowledge-base search with PostgreSQL full-text search (english configuration).

Query rule (documented in README):
- Terms are taken from subject + body, lower-cased, letters/digits only.
- Every 3-digit HTTP status code (e.g. 401, 403, 429) is kept as a term.
- Terms are OR-ed; ts_rank orders results, ties are broken by section ID (stable order).
- Overlap rule: a section is kept only if at least `min_terms` distinct query terms match it.
  (An absolute ts_rank floor was tried first and rejected: one common word such as "order"
  scored higher than a real match on a short ticket.) If nothing is left, the ticket goes to
  manual review by rule NO_KB_MATCH and the model is not called.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import psycopg

MAX_TERMS = 60
_TOKEN = re.compile(r"[a-z0-9]+")
_STATUS_CODE = re.compile(r"\b[1-5]\d\d\b")
# Small extra stoplist on top of PostgreSQL's english stopwords: words that appear in nearly every
# support ticket and would otherwise match every section.
_NOISE = {
    "hello", "hi", "thanks", "thank", "please", "regards", "team", "support", "help",
    "issue", "problem", "question", "since", "today", "yesterday", "anyone", "could",
    "would", "also", "still", "get", "getting", "got", "use", "using", "api", "fernwick",
}


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    body: str
    tags: list[str]
    rank: float
    matched_terms: int = 0

    @property
    def url(self) -> str:
        return f"/kb/{self.id}"


def build_query_terms(subject: str, body: str) -> list[str]:
    text = f"{subject}\n{body}".lower()
    terms: list[str] = []
    seen: set[str] = set()
    for code in _STATUS_CODE.findall(text):
        if code not in seen:
            seen.add(code)
            terms.append(code)
    for tok in _TOKEN.findall(text):
        if tok in seen or tok in _NOISE:
            continue
        if tok.isdigit() or len(tok) < 3:
            continue  # other numbers and very short tokens add noise, not signal
        seen.add(tok)
        terms.append(tok)
    return terms[:MAX_TERMS]


def to_tsquery_text(terms: list[str]) -> str:
    # Terms are [a-z0-9]+ only, so they cannot carry tsquery operators.
    return " | ".join(terms)


def search(
    conn: psycopg.Connection,
    subject: str,
    body: str,
    *,
    top_k: int,
    min_terms: int,
    max_chars: int,
) -> list[Section]:
    terms = build_query_terms(subject, body)
    if not terms:
        return []
    rows = conn.execute(
        """
        WITH q AS (SELECT to_tsquery('english', %(q)s) AS query),
             t AS (SELECT DISTINCT to_tsquery('english', term) AS tq
                   FROM unnest(%(terms)s::text[]) AS term
                   WHERE numnode(to_tsquery('english', term)) > 0)  -- drops stopwords
        SELECT s.id, s.title, s.body, s.tags,
               round(ts_rank(s.tsv, q.query)::numeric, 6) AS rank,
               (SELECT count(*) FROM t WHERE s.tsv @@ t.tq) AS matched
        FROM kb_sections s, q
        WHERE s.tsv @@ q.query
        """,
        {"q": to_tsquery_text(terms), "terms": terms},
    ).fetchall()
    rows = [r for r in rows if r["matched"] >= min_terms]
    rows.sort(key=lambda r: (-r["rank"], r["id"]))
    out = []
    for r in rows[:top_k]:
        text = r["body"]
        if len(text) > max_chars:
            text = text[: max_chars - 3].rstrip() + "..."
        out.append(Section(r["id"], r["title"], text, list(r["tags"]), float(r["rank"]), int(r["matched"])))
    return out


def get_section(conn: psycopg.Connection, section_id: str) -> dict | None:
    return conn.execute(
        "SELECT id, title, body, tags, updated_at FROM kb_sections WHERE id = %s", (section_id,)
    ).fetchone()


def list_sections(conn: psycopg.Connection) -> list[dict]:
    return conn.execute("SELECT id, title, tags FROM kb_sections ORDER BY id").fetchall()
