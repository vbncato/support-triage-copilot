-- Support Triage Copilot schema.
-- Applied idempotently by the application at startup (app/db.py: apply_schema).
-- Every statement must be safe to run repeatedly.

CREATE TABLE IF NOT EXISTS kb_sections (
    id           text PRIMARY KEY CHECK (id ~ '^kb-[a-z0-9-]+$'),
    title        text NOT NULL,
    body         text NOT NULL,
    tags         text[] NOT NULL DEFAULT '{}',
    tags_text    text NOT NULL DEFAULT '',
    content_hash text NOT NULL,
    tsv          tsvector GENERATED ALWAYS AS (
                     setweight(to_tsvector('english', title), 'A') ||
                     setweight(to_tsvector('english', tags_text), 'B') ||
                     setweight(to_tsvector('english', body), 'C')
                 ) STORED,
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS kb_sections_tsv_idx ON kb_sections USING gin (tsv);

CREATE TABLE IF NOT EXISTS tickets (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    external_id        text UNIQUE CHECK (external_id IS NULL OR length(external_id) BETWEEN 1 AND 64),
    subject            text NOT NULL CHECK (length(btrim(subject)) BETWEEN 1 AND 200),
    body               text NOT NULL CHECK (length(btrim(body)) BETWEEN 1 AND 5000),
    status             text NOT NULL CHECK (status IN
                         ('NEW', 'ANALYZING', 'READY_FOR_REVIEW', 'MANUAL_REVIEW', 'ANALYSIS_FAILED', 'RESOLVED')),
    version            integer NOT NULL DEFAULT 1,
    current_attempt_id bigint,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tickets_status_idx ON tickets (status, updated_at DESC);

CREATE TABLE IF NOT EXISTS analysis_attempts (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticket_id         bigint NOT NULL REFERENCES tickets (id) ON DELETE CASCADE,
    attempt_no        integer NOT NULL,
    mode              text NOT NULL CHECK (mode IN ('demo', 'live')),
    model             text NOT NULL,
    state             text NOT NULL CHECK (state IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'SUPERSEDED', 'EXPIRED')),
    started_at        timestamptz NOT NULL DEFAULT now(),
    deadline_at       timestamptz NOT NULL,
    finished_at       timestamptz,
    -- Sections passed to the model: [{"id", "title", "rank"}]. Only these IDs may be cited.
    retrieved         jsonb NOT NULL DEFAULT '[]'::jsonb,
    decided_by        text CHECK (decided_by IN ('model', 'rule')),
    outcome           text CHECK (outcome IN ('DRAFT_REPLY', 'NEEDS_INFO', 'MANUAL_REVIEW')),
    error_type        text,
    error_detail      text,
    raw_output        text,
    proposal          jsonb,
    validation_errors jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- A result that arrived after the attempt stopped being current is kept here and never applied.
    late_result       jsonb,
    UNIQUE (ticket_id, attempt_no)
);
CREATE INDEX IF NOT EXISTS attempts_running_idx ON analysis_attempts (deadline_at) WHERE state = 'RUNNING';

CREATE TABLE IF NOT EXISTS ticket_events (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticket_id   bigint NOT NULL REFERENCES tickets (id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    actor       text NOT NULL CHECK (actor IN ('system', 'operator')),
    action      text NOT NULL,
    from_status text,
    to_status   text,
    attempt_id  bigint,
    detail      jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS ticket_events_ticket_idx ON ticket_events (ticket_id, id);

-- One final operator decision per ticket. The primary key makes a second approval impossible.
CREATE TABLE IF NOT EXISTS decisions (
    ticket_id      bigint PRIMARY KEY REFERENCES tickets (id) ON DELETE CASCADE,
    action         text NOT NULL CHECK (action IN ('APPROVE', 'EDIT_APPROVE')),
    attempt_id     bigint,
    final_text     text NOT NULL CHECK (length(btrim(final_text)) BETWEEN 1 AND 5000),
    edited         boolean NOT NULL,
    disposition    text,
    category       text,
    queue          text,
    ticket_version integer NOT NULL,
    decided_at     timestamptz NOT NULL DEFAULT now()
);
