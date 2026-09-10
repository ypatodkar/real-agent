PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS interview_sessions (
    id TEXT PRIMARY KEY,
    seed TEXT NOT NULL DEFAULT '',
    storytelling_format TEXT NOT NULL DEFAULT 'not_sure',
    involvement INTEGER NOT NULL DEFAULT 50 CHECK (involvement BETWEEN 0 AND 100),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'ready_for_outline', 'outline_started', 'archived')),
    revision INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Immutable. What the filmmaker did, never a paraphrase of it.
CREATE TABLE IF NOT EXISTS interview_events (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN (
        'interview_started', 'message_submitted', 'suggestions_requested',
        'suggestions_selected', 'suggestions_rejected', 'question_skipped',
        'continue_interview', 'decision_revised')),
    payload_json TEXT NOT NULL DEFAULT '{}',
    expected_revision INTEGER,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'completed', 'failed')),
    response_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (session_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS assistant_responses (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
    addressed_event_id TEXT NOT NULL REFERENCES interview_events(id),
    primary_intent TEXT NOT NULL CHECK (primary_intent IN (
        'ask_question', 'offer_suggestions', 'reflect_and_confirm',
        'coach_writer', 'recommend_outline')),
    blocks_json TEXT NOT NULL DEFAULT '[]',
    question TEXT NOT NULL DEFAULT '',
    focus TEXT NOT NULL DEFAULT 'story',
    degraded INTEGER NOT NULL DEFAULT 0 CHECK (degraded IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suggestions (
    id TEXT PRIMARY KEY,
    response_id TEXT NOT NULL REFERENCES assistant_responses(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'shown'
        CHECK (status IN ('shown', 'selected', 'rejected', 'superseded', 'undecided'))
);

CREATE TABLE IF NOT EXISTS suggestion_selections (
    suggestion_id TEXT NOT NULL REFERENCES suggestions(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL REFERENCES interview_events(id) ON DELETE CASCADE,
    note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (suggestion_id, event_id)
);

-- Append-only. A correction supersedes; it never overwrites.
CREATE TABLE IF NOT EXISTS story_facts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    source_event_id TEXT NOT NULL REFERENCES interview_events(id),
    evidence TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'superseded', 'retracted')),
    superseded_by TEXT REFERENCES story_facts(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS story_gaps (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    evidence TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS readiness_assessments (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
    event_id TEXT REFERENCES interview_events(id),
    ready INTEGER NOT NULL DEFAULT 0 CHECK (ready IN (0, 1)),
    lenses_json TEXT NOT NULL DEFAULT '{}',
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL REFERENCES interview_events(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'expired')),
    model_calls INTEGER NOT NULL DEFAULT 0,
    repairs INTEGER NOT NULL DEFAULT 0,
    cost_micro_usd INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    outcome TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS agent_steps (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    rejected TEXT,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_micro_usd INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, seq)
);

CREATE TABLE IF NOT EXISTS telemetry_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0 CHECK (sent IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_session ON interview_events(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_responses_session ON assistant_responses(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_facts_active ON story_facts(session_id, status);
