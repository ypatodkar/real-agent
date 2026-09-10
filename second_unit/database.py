"""Canonical SQLite persistence for the Interview application."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from contextlib import contextmanager

from .domain import (
    READINESS_LENSES,
    InterviewEvent,
    TurnDecision,
    normalize_story_text,
)


SCHEMA_VERSION = 8


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS interview_sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    seed TEXT NOT NULL,
    storytelling_format TEXT NOT NULL
        CHECK (storytelling_format IN ('narrated', 'dialogue_led', 'hybrid', 'not_sure')),
    involvement_mode TEXT NOT NULL
        CHECK (involvement_mode IN ('ai_led', 'collaborative', 'author_led')),
    question_target INTEGER NOT NULL DEFAULT 8
        CHECK (question_target BETWEEN 3 AND 20),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'ready_for_outline', 'archived')),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    readiness_score REAL NOT NULL DEFAULT 0 CHECK (readiness_score BETWEEN 0 AND 1),
    readiness_reason TEXT NOT NULL DEFAULT 'The story has not been assessed yet.',
    current_response_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS interview_events (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    expected_revision INTEGER NOT NULL CHECK (expected_revision >= 0),
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'completed', 'failed_retryable')),
    result_revision INTEGER,
    result_json TEXT CHECK (result_json IS NULL OR json_valid(result_json)),
    safe_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    UNIQUE (session_id, sequence)
);

CREATE TABLE IF NOT EXISTS assistant_responses (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL UNIQUE REFERENCES interview_events(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    intent TEXT NOT NULL,
    guidance TEXT NOT NULL DEFAULT '',
    question TEXT NOT NULL DEFAULT '',
    focus TEXT NOT NULL DEFAULT 'story',
    listening_for TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (session_id, sequence)
);

CREATE TABLE IF NOT EXISTS interview_questions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
    response_id TEXT NOT NULL REFERENCES assistant_responses(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 20),
    text TEXT NOT NULL,
    explanation TEXT NOT NULL,
    focus TEXT NOT NULL DEFAULT 'story',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (response_id, position)
);

CREATE TABLE IF NOT EXISTS suggestions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
    response_id TEXT NOT NULL REFERENCES assistant_responses(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 3),
    label TEXT NOT NULL,
    detail TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'shown'
        CHECK (status IN ('shown', 'selected', 'rejected', 'superseded')),
    resolved_event_id TEXT REFERENCES interview_events(id),
    writer_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TEXT,
    UNIQUE (response_id, position)
);

CREATE TABLE IF NOT EXISTS story_facts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'superseded', 'retracted')),
    source_event_id TEXT NOT NULL REFERENCES interview_events(id),
    source_suggestion_id TEXT REFERENCES suggestions(id),
    evidence TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_fact
ON story_facts(session_id, normalized_text) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS story_gaps (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    normalized_description TEXT NOT NULL,
    impact TEXT NOT NULL DEFAULT 'blocking',
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    opened_event_id TEXT NOT NULL REFERENCES interview_events(id),
    resolved_event_id TEXT REFERENCES interview_events(id),
    evidence TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_open_gap
ON story_gaps(session_id, normalized_description) WHERE status = 'open';

CREATE TABLE IF NOT EXISTS readiness_assessments (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL UNIQUE REFERENCES interview_events(id),
    response_id TEXT NOT NULL REFERENCES assistant_responses(id),
    score REAL NOT NULL CHECK (score BETWEEN 0 AND 1),
    lenses_json TEXT NOT NULL CHECK (json_valid(lenses_json)),
    reason TEXT NOT NULL,
    recommend_outline INTEGER NOT NULL CHECK (recommend_outline IN (0, 1)),
    resulting_status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL UNIQUE REFERENCES interview_events(id) ON DELETE CASCADE,
    base_revision INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'expired')),
    model_attempts INTEGER NOT NULL DEFAULT 0,
    backend TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    safe_error TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_session_run
ON agent_runs(session_id) WHERE status IN ('pending', 'running');

CREATE INDEX IF NOT EXISTS idx_events_session_sequence
ON interview_events(session_id, sequence);
CREATE INDEX IF NOT EXISTS idx_responses_session_sequence
ON assistant_responses(session_id, sequence);
CREATE INDEX IF NOT EXISTS idx_suggestions_session_status
ON suggestions(session_id, status);
CREATE INDEX IF NOT EXISTS idx_facts_session_status
ON story_facts(session_id, status);
CREATE INDEX IF NOT EXISTS idx_gaps_session_status
ON story_gaps(session_id, status);

CREATE TABLE IF NOT EXISTS outlines (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE
        REFERENCES interview_sessions(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'approved', 'archived')),
    active_structure_id TEXT REFERENCES outline_structures(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS outline_structures (
    id TEXT PRIMARY KEY,
    outline_id TEXT NOT NULL REFERENCES outlines(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 5),
    kind TEXT NOT NULL
        CHECK (kind IN ('acts', 'sequences', 'visual_progression',
                        'narration_led', 'hybrid', 'custom')),
    label TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL
        CHECK (origin IN ('filmmaker', 'agent', 'system')),
    approval_status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (approval_status IN ('proposed', 'accepted', 'rejected')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (outline_id, position)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_outline_accepted_structure
ON outline_structures(outline_id) WHERE approval_status = 'accepted';

CREATE TABLE IF NOT EXISTS outline_beats (
    id TEXT PRIMARY KEY,
    outline_id TEXT NOT NULL REFERENCES outlines(id) ON DELETE CASCADE,
    structure_id TEXT NOT NULL REFERENCES outline_structures(id),
    position INTEGER NOT NULL CHECK (position >= 1),
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL
        CHECK (origin IN ('filmmaker', 'agent', 'interview')),
    approval_status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (approval_status IN ('proposed', 'accepted', 'rejected')),
    source_refs_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(source_refs_json)),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_outline_beat_position
ON outline_beats(outline_id, position) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS outline_approvals (
    id TEXT PRIMARY KEY,
    outline_id TEXT NOT NULL REFERENCES outlines(id) ON DELETE CASCADE,
    target_kind TEXT NOT NULL CHECK (target_kind IN ('structure', 'beat', 'outline')),
    target_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('accepted', 'rejected', 'reopened')),
    actor TEXT NOT NULL CHECK (actor IN ('filmmaker', 'agent', 'system')),
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS outline_versions (
    id TEXT PRIMARY KEY,
    outline_id TEXT NOT NULL REFERENCES outlines(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    operation TEXT NOT NULL,
    actor TEXT NOT NULL CHECK (actor IN ('filmmaker', 'agent', 'system')),
    snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (outline_id, revision)
);

CREATE TABLE IF NOT EXISTS outline_agent_runs (
    id TEXT PRIMARY KEY,
    outline_id TEXT NOT NULL REFERENCES outlines(id) ON DELETE CASCADE,
    base_revision INTEGER NOT NULL CHECK (base_revision >= 0),
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'completed', 'failed')),
    model_attempts INTEGER NOT NULL DEFAULT 0,
    backend TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    error_code TEXT NOT NULL DEFAULT '',
    provider_error_type TEXT NOT NULL DEFAULT '',
    safe_error TEXT NOT NULL DEFAULT '',
    diagnostics_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(diagnostics_json)),
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_outline_structures_outline
ON outline_structures(outline_id, position);
CREATE INDEX IF NOT EXISTS idx_outline_beats_outline_status_position
ON outline_beats(outline_id, status, position);
CREATE INDEX IF NOT EXISTS idx_outline_versions_outline_revision
ON outline_versions(outline_id, revision);
CREATE INDEX IF NOT EXISTS idx_outline_agent_runs_outline_started
ON outline_agent_runs(outline_id, started_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_running_outline_agent
ON outline_agent_runs(outline_id) WHERE status = 'running';

CREATE TABLE IF NOT EXISTS screenplays (
    id TEXT PRIMARY KEY,
    outline_id TEXT NOT NULL UNIQUE REFERENCES outlines(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    mode TEXT CHECK (mode IN ('visual', 'narration_led', 'character_led', 'hybrid')),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'archived')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS screenplay_scenes (
    id TEXT PRIMARY KEY,
    screenplay_id TEXT NOT NULL REFERENCES screenplays(id) ON DELETE CASCADE,
    beat_id TEXT REFERENCES outline_beats(id),
    position INTEGER NOT NULL CHECK (position >= 1),
    heading TEXT NOT NULL,
    action TEXT NOT NULL,
    narration TEXT NOT NULL DEFAULT '',
    dialogue_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(dialogue_json)),
    approval_status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (approval_status IN ('proposed', 'accepted', 'rejected')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_screenplay_scene_position
ON screenplay_scenes(screenplay_id, position) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS screenplay_versions (
    id TEXT PRIMARY KEY,
    screenplay_id TEXT NOT NULL REFERENCES screenplays(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    operation TEXT NOT NULL,
    actor TEXT NOT NULL CHECK (actor IN ('filmmaker', 'agent', 'system')),
    snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (screenplay_id, revision)
);

CREATE TABLE IF NOT EXISTS screenplay_agent_runs (
    id TEXT PRIMARY KEY,
    screenplay_id TEXT NOT NULL REFERENCES screenplays(id) ON DELETE CASCADE,
    base_revision INTEGER NOT NULL CHECK (base_revision >= 0),
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'completed', 'failed')),
    model_attempts INTEGER NOT NULL DEFAULT 0,
    backend TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL,
    safe_error TEXT NOT NULL DEFAULT '',
    diagnostics_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(diagnostics_json)),
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_screenplay_agent_runs_screenplay_started
ON screenplay_agent_runs(screenplay_id, started_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_running_screenplay_agent
ON screenplay_agent_runs(screenplay_id) WHERE status = 'running';

CREATE TABLE IF NOT EXISTS production_breakdowns (
    id TEXT PRIMARY KEY,
    screenplay_id TEXT NOT NULL UNIQUE REFERENCES screenplays(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'approved', 'archived')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS breakdown_items (
    id TEXT PRIMARY KEY,
    breakdown_id TEXT NOT NULL REFERENCES production_breakdowns(id) ON DELETE CASCADE,
    scene_id TEXT NOT NULL REFERENCES screenplay_scenes(id),
    position INTEGER NOT NULL CHECK (position >= 1),
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    approval_status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (approval_status IN ('proposed', 'accepted')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS breakdown_versions (
    id TEXT PRIMARY KEY,
    breakdown_id TEXT NOT NULL REFERENCES production_breakdowns(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    operation TEXT NOT NULL,
    actor TEXT NOT NULL CHECK (actor IN ('filmmaker', 'agent', 'system')),
    snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (breakdown_id, revision)
);

CREATE TABLE IF NOT EXISTS breakdown_agent_runs (
    id TEXT PRIMARY KEY,
    screenplay_id TEXT NOT NULL REFERENCES screenplays(id) ON DELETE CASCADE,
    base_revision INTEGER NOT NULL CHECK (base_revision >= 0),
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'completed', 'failed')),
    model_attempts INTEGER NOT NULL DEFAULT 0,
    backend TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL,
    safe_error TEXT NOT NULL DEFAULT '',
    diagnostics_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(diagnostics_json)),
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_breakdown_items_breakdown_scene_position
ON breakdown_items(breakdown_id, scene_id, position);
CREATE INDEX IF NOT EXISTS idx_breakdown_versions_revision
ON breakdown_versions(breakdown_id, revision);
CREATE INDEX IF NOT EXISTS idx_breakdown_agent_runs_started
ON breakdown_agent_runs(screenplay_id, started_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_running_breakdown_agent
ON breakdown_agent_runs(screenplay_id) WHERE status = 'running';

CREATE TABLE IF NOT EXISTS location_searches (
    id TEXT PRIMARY KEY,
    breakdown_id TEXT NOT NULL REFERENCES production_breakdowns(id) ON DELETE CASCADE,
    base_place_id TEXT NOT NULL,
    max_travel_minutes INTEGER NOT NULL CHECK (max_travel_minutes BETWEEN 5 AND 180),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS location_candidates (
    id TEXT PRIMARY KEY,
    search_id TEXT NOT NULL REFERENCES location_searches(id) ON DELETE CASCADE,
    breakdown_item_id TEXT NOT NULL REFERENCES breakdown_items(id),
    place_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'suggested'
        CHECK (status IN ('suggested', 'shortlisted', 'rejected')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (search_id, breakdown_item_id, place_id)
);

CREATE INDEX IF NOT EXISTS idx_location_searches_breakdown_created
ON location_searches(breakdown_id, created_at);
CREATE INDEX IF NOT EXISTS idx_location_candidates_search_item
ON location_candidates(search_id, breakdown_item_id);
"""


class RepositoryError(RuntimeError):
    code = "repository_error"


class NotFound(RepositoryError):
    code = "not_found"


class Conflict(RepositoryError):
    code = "conflict"


class StaleRevision(Conflict):
    code = "stale_revision"

    def __init__(self, current_revision: int):
        self.current_revision = current_revision
        super().__init__(f"session changed; current revision is {current_revision}")


class SessionBusy(Conflict):
    code = "session_busy"


class InvalidTransition(RepositoryError):
    code = "invalid_transition"


@dataclass(frozen=True)
class IngestResult:
    event: InterviewEvent
    replayed_result: dict[str, Any] | None = None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class Repository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA busy_timeout = 5000")
        return db

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def migrate(self) -> None:
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            db.executescript(SCHEMA)
            columns = {
                row["name"] for row in db.execute("PRAGMA table_info(interview_sessions)")
            }
            if "question_target" not in columns:
                db.execute(
                    """ALTER TABLE interview_sessions ADD COLUMN question_target
                       INTEGER NOT NULL DEFAULT 8 CHECK (question_target BETWEEN 3 AND 20)"""
                )
            db.execute("DELETE FROM schema_migrations WHERE version != ?", (SCHEMA_VERSION,))
            db.execute(
                "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
                (SCHEMA_VERSION,),
            )

    def recover_interrupted_runs(self) -> int:
        """Make events abandoned by a previous process safely retryable.

        This is called once during application startup, before the HTTP server
        accepts work. A live process never calls it against another live run.
        """
        message = "The previous process stopped during this turn. Your input is saved and can be retried."
        with self.transaction(immediate=True) as db:
            db.execute(
                """UPDATE outline_agent_runs SET status = 'failed',
                   error_code = 'process_interrupted',
                   safe_error = 'The process stopped during Outline generation.',
                   finished_at = CURRENT_TIMESTAMP WHERE status = 'running'"""
            )
            db.execute(
                """UPDATE screenplay_agent_runs SET status = 'failed',
                   safe_error = 'The process stopped during Screenplay generation.',
                   finished_at = CURRENT_TIMESTAMP WHERE status = 'running'"""
            )
            db.execute(
                """UPDATE breakdown_agent_runs SET status = 'failed',
                   safe_error = 'The process stopped during Breakdown generation.',
                   finished_at = CURRENT_TIMESTAMP WHERE status = 'running'"""
            )
            rows = db.execute(
                "SELECT event_id FROM agent_runs WHERE status IN ('pending', 'running')"
            ).fetchall()
            event_ids = [row["event_id"] for row in rows]
            if not event_ids:
                return 0
            marks = ",".join("?" for _ in event_ids)
            db.execute(
                f"""UPDATE interview_events SET status = 'failed_retryable', safe_error = ?
                    WHERE id IN ({marks}) AND status IN ('pending', 'processing')""",
                (message, *event_ids),
            )
            db.execute(
                f"""UPDATE agent_runs SET status = 'failed', safe_error = ?,
                    finished_at = CURRENT_TIMESTAMP WHERE event_id IN ({marks})
                    AND status IN ('pending', 'running')""",
                (message, *event_ids),
            )
            return len(event_ids)

    def start_outline_agent_run(
        self, outline_id: str, base_revision: int, prompt_version: str,
        diagnostics: list[dict[str, Any]],
    ) -> str:
        run_id = _record_id("outline_run")
        with self.transaction(immediate=True) as db:
            running = db.execute(
                "SELECT id FROM outline_agent_runs WHERE outline_id = ? AND status = 'running'",
                (outline_id,),
            ).fetchone()
            if running:
                raise SessionBusy("Outline generation is already running")
            db.execute(
                """INSERT INTO outline_agent_runs
                   (id, outline_id, base_revision, prompt_version, diagnostics_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_id, outline_id, base_revision, prompt_version, _json(diagnostics)),
            )
        return run_id

    def update_outline_agent_run(
        self, run_id: str, *, status: str = "running", model_attempts: int = 0,
        backend: str = "", model: str = "", prompt_tokens: int = 0,
        output_tokens: int = 0, error_code: str = "",
        provider_error_type: str = "", safe_error: str = "",
        diagnostics: list[dict[str, Any]] | None = None,
    ) -> None:
        if status not in {"running", "completed", "failed"}:
            raise ValueError("invalid Outline agent run status")
        with self.transaction(immediate=True) as db:
            updated = db.execute(
                """UPDATE outline_agent_runs SET status = ?, model_attempts = ?,
                   backend = ?, model = ?, prompt_tokens = ?, output_tokens = ?,
                   error_code = ?, provider_error_type = ?, safe_error = ?,
                   diagnostics_json = ?,
                   finished_at = CASE WHEN ? = 'running' THEN NULL ELSE CURRENT_TIMESTAMP END
                   WHERE id = ?""",
                (
                    status, model_attempts, backend, model, prompt_tokens,
                    output_tokens, error_code, provider_error_type,
                    safe_error[:4000], _json(diagnostics or []), status, run_id,
                ),
            )
            if updated.rowcount != 1:
                raise NotFound("Outline agent run not found")

    def start_screenplay_agent_run(
        self, screenplay_id: str, base_revision: int, prompt_version: str,
        diagnostics: list[dict[str, Any]],
    ) -> str:
        run_id = _record_id("screenplay_run")
        with self.transaction(immediate=True) as db:
            running = db.execute(
                "SELECT id FROM screenplay_agent_runs WHERE screenplay_id = ? AND status = 'running'",
                (screenplay_id,),
            ).fetchone()
            if running:
                raise SessionBusy("Screenplay generation is already running")
            db.execute(
                """INSERT INTO screenplay_agent_runs
                   (id, screenplay_id, base_revision, prompt_version, diagnostics_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_id, screenplay_id, base_revision, prompt_version, _json(diagnostics)),
            )
        return run_id

    def update_screenplay_agent_run(
        self, run_id: str, *, status: str = "running", model_attempts: int = 0,
        backend: str = "", model: str = "", safe_error: str = "",
        diagnostics: list[dict[str, Any]] | None = None,
    ) -> None:
        if status not in {"running", "completed", "failed"}:
            raise ValueError("invalid Screenplay agent run status")
        with self.transaction(immediate=True) as db:
            updated = db.execute(
                """UPDATE screenplay_agent_runs SET status = ?, model_attempts = ?,
                   backend = ?, model = ?, safe_error = ?, diagnostics_json = ?,
                   finished_at = CASE WHEN ? = 'running' THEN NULL ELSE CURRENT_TIMESTAMP END
                   WHERE id = ?""",
                (
                    status, model_attempts, backend, model, safe_error[:4000],
                    _json(diagnostics or []), status, run_id,
                ),
            )
            if updated.rowcount != 1:
                raise NotFound("Screenplay agent run not found")

    def start_breakdown_agent_run(
        self, screenplay_id: str, base_revision: int, prompt_version: str,
        diagnostics: list[dict[str, Any]],
    ) -> str:
        run_id = _record_id("breakdown_run")
        with self.transaction(immediate=True) as db:
            running = db.execute(
                "SELECT id FROM breakdown_agent_runs WHERE screenplay_id = ? AND status = 'running'",
                (screenplay_id,),
            ).fetchone()
            if running:
                raise SessionBusy("Breakdown generation is already running")
            db.execute(
                """INSERT INTO breakdown_agent_runs
                   (id, screenplay_id, base_revision, prompt_version, diagnostics_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_id, screenplay_id, base_revision, prompt_version, _json(diagnostics)),
            )
        return run_id

    def update_breakdown_agent_run(
        self, run_id: str, *, status: str = "running", model_attempts: int = 0,
        backend: str = "", model: str = "", safe_error: str = "",
        diagnostics: list[dict[str, Any]] | None = None,
    ) -> None:
        if status not in {"running", "completed", "failed"}:
            raise ValueError("invalid Breakdown agent run status")
        with self.transaction(immediate=True) as db:
            updated = db.execute(
                """UPDATE breakdown_agent_runs SET status = ?, model_attempts = ?,
                   backend = ?, model = ?, safe_error = ?, diagnostics_json = ?,
                   finished_at = CASE WHEN ? = 'running' THEN NULL ELSE CURRENT_TIMESTAMP END
                   WHERE id = ?""",
                (
                    status, model_attempts, backend, model, safe_error[:4000],
                    _json(diagnostics or []), status, run_id,
                ),
            )
            if updated.rowcount != 1:
                raise NotFound("Breakdown agent run not found")

    def create_and_ingest_start(self, event: InterviewEvent) -> IngestResult:
        payload = event.payload
        with self.transaction(immediate=True) as db:
            existing_event = db.execute(
                "SELECT * FROM interview_events WHERE id = ?", (event.id,)
            ).fetchone()
            existing = db.execute(
                "SELECT id FROM interview_sessions WHERE id = ?", (event.session_id,)
            ).fetchone()
            if existing:
                identical = bool(existing_event) and (
                    existing_event["session_id"] == event.session_id
                    and existing_event["expected_revision"] == 0
                    and existing_event["kind"] == event.kind
                    and existing_event["payload_json"] == _json(event.payload)
                )
                if not identical:
                    raise Conflict("session or event ID was already used for different input")
                if existing_event["status"] == "completed" and existing_event["result_json"]:
                    return IngestResult(
                        event, self._project_session(db, event.session_id)
                    )
                if existing_event["status"] == "processing":
                    raise SessionBusy("this Interview is already being created")
                if existing_event["status"] == "failed_retryable":
                    db.execute(
                        """UPDATE agent_runs SET status = 'running', safe_error = '',
                           started_at = CURRENT_TIMESTAMP, finished_at = NULL
                           WHERE event_id = ?""",
                        (event.id,),
                    )
                    db.execute(
                        "UPDATE interview_events SET status = 'processing', safe_error = '' WHERE id = ?",
                        (event.id,),
                    )
                    return IngestResult(event)
                raise SessionBusy("this Interview is pending")
            if existing_event:
                raise Conflict("event ID was already used for another Interview")
            db.execute(
                """INSERT INTO interview_sessions
                   (id, title, seed, storytelling_format, involvement_mode,
                    question_target)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    event.session_id, payload["title"], payload["seed"],
                    payload["storytelling_format"], payload["involvement_mode"],
                    payload["question_target"],
                ),
            )
            self._insert_event_and_run(db, event, sequence=1)
        return IngestResult(event)

    def ingest_event(self, event: InterviewEvent) -> IngestResult:
        signature = _json(event.payload)
        with self.transaction(immediate=True) as db:
            existing = db.execute(
                "SELECT * FROM interview_events WHERE id = ?", (event.id,)
            ).fetchone()
            if existing:
                identical = (
                    existing["session_id"] == event.session_id
                    and existing["expected_revision"] == event.expected_revision
                    and existing["kind"] == event.kind
                    and existing["payload_json"] == signature
                )
                if not identical:
                    raise Conflict("event ID was already used for different input")
                if existing["status"] == "completed" and existing["result_json"]:
                    return IngestResult(
                        event, self._project_session(db, event.session_id)
                    )
                if existing["status"] == "processing":
                    raise SessionBusy("this event is already being processed")
                if existing["status"] == "failed_retryable":
                    session = self._session_row(db, event.session_id)
                    if session["revision"] != event.expected_revision:
                        raise StaleRevision(session["revision"])
                    try:
                        db.execute(
                            """UPDATE agent_runs SET status = 'running', safe_error = '',
                               started_at = CURRENT_TIMESTAMP, finished_at = NULL
                               WHERE event_id = ?""",
                            (event.id,),
                        )
                    except sqlite3.IntegrityError as exc:
                        raise SessionBusy("another Interview event is already running") from exc
                    db.execute(
                        "UPDATE interview_events SET status = 'processing', safe_error = '' WHERE id = ?",
                        (event.id,),
                    )
                    return IngestResult(event)
                raise SessionBusy("event is pending")

            session = self._session_row(db, event.session_id)
            if session["revision"] != event.expected_revision:
                raise StaleRevision(session["revision"])
            if session["status"] == "archived":
                raise InvalidTransition("an archived Interview cannot receive new events")
            if event.kind == "continue_interview" and session["status"] != "ready_for_outline":
                raise InvalidTransition(
                    "only an outline-ready Interview can continue development"
                )
            if session["status"] == "ready_for_outline" and event.kind not in {
                "continue_interview", "message_submitted", "questionnaire_revised",
                "question_ideas_requested",
            }:
                raise InvalidTransition(
                    "keep developing the Interview before using this action"
                )
            sequence = db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM interview_events WHERE session_id = ?",
                (event.session_id,),
            ).fetchone()[0]
            self._insert_event_and_run(db, event, sequence=sequence)
        return IngestResult(event)

    def _insert_event_and_run(self, db: sqlite3.Connection, event: InterviewEvent,
                              *, sequence: int) -> None:
        try:
            db.execute(
                """INSERT INTO interview_events
                   (id, session_id, sequence, expected_revision, kind, payload_json, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'processing')""",
                (
                    event.id, event.session_id, sequence, event.expected_revision,
                    event.kind, _json(event.payload),
                ),
            )
            db.execute(
                """INSERT INTO agent_runs
                   (id, session_id, event_id, base_revision, status)
                   VALUES (?, ?, ?, ?, 'running')""",
                (_record_id("run"), event.session_id, event.id, event.expected_revision),
            )
        except sqlite3.IntegrityError as exc:
            if "uq_active_session_run" in str(exc) or "UNIQUE constraint failed: agent_runs.session_id" in str(exc):
                raise SessionBusy("another Interview event is already running") from exc
            raise

    def load_snapshot(self, session_id: str, *, event_id: str | None = None) -> dict[str, Any]:
        with self.connect() as db:
            session = dict(self._session_row(db, session_id))
            events = db.execute(
                """SELECT e.*, r.id AS response_id, r.intent, r.guidance, r.question,
                          r.focus, r.listening_for
                   FROM interview_events e
                   LEFT JOIN assistant_responses r ON r.event_id = e.id
                   WHERE e.session_id = ? ORDER BY e.sequence DESC LIMIT 12""",
                (session_id,),
            ).fetchall()
            recent = []
            for row in reversed(events):
                item = dict(row)
                item["payload"] = json.loads(item.pop("payload_json"))
                item.pop("result_json", None)
                item["questions"] = [dict(question) for question in db.execute(
                    """SELECT id, position, text, explanation, focus
                       FROM interview_questions WHERE response_id = ? ORDER BY position""",
                    (item.get("response_id"),),
                )] if item.get("response_id") else []
                recent.append(item)
            facts = [dict(row) for row in db.execute(
                "SELECT * FROM story_facts WHERE session_id = ? AND status = 'active' ORDER BY created_at, rowid",
                (session_id,),
            )]
            gaps = [dict(row) for row in db.execute(
                "SELECT * FROM story_gaps WHERE session_id = ? AND status = 'open' ORDER BY created_at, rowid",
                (session_id,),
            )]
            suggestions = [dict(row) for row in db.execute(
                """SELECT * FROM suggestions WHERE session_id = ?
                   ORDER BY created_at DESC, position LIMIT 18""",
                (session_id,),
            )]
            latest_event = None
            if event_id:
                row = db.execute(
                    "SELECT * FROM interview_events WHERE id = ? AND session_id = ?",
                    (event_id, session_id),
                ).fetchone()
                if not row:
                    raise NotFound("event not found")
                latest_event = dict(row)
                latest_event["payload"] = json.loads(latest_event.pop("payload_json"))
                latest_event.pop("result_json", None)
            return {
                "session": session,
                "latest_event": latest_event,
                "recent_events": recent,
                "facts": facts,
                "gaps": gaps,
                "suggestions": suggestions,
                "recent_questions": [
                    question
                    for item in recent
                    for question in (
                        [item["question"]] if item.get("question") else []
                    ) + [planned["text"] for planned in item.get("questions", [])]
                ][-20:],
                "questions_asked": db.execute(
                    """SELECT
                         (SELECT COUNT(*) FROM assistant_responses
                          WHERE session_id = ? AND trim(question) != '') +
                         (SELECT COUNT(*) FROM interview_questions
                          WHERE session_id = ?)""",
                    (session_id, session_id),
                ).fetchone()[0],
            }

    def validate_event_references(self, event: InterviewEvent) -> dict[str, Any]:
        """Resolve IDs used by an action before a provider call."""
        with self.connect() as db:
            context: dict[str, Any] = {}
            session = self._session_row(db, event.session_id)
            current_response_id = session["current_response_id"]
            if event.kind in {"suggestions_selected", "suggestions_rejected"}:
                ids = event.payload["suggestion_ids"]
                marks = ",".join("?" for _ in ids)
                rows = db.execute(
                    f"""SELECT * FROM suggestions
                        WHERE session_id = ? AND id IN ({marks})""",
                    (event.session_id, *ids),
                ).fetchall()
                by_id = {row["id"]: dict(row) for row in rows}
                if set(by_id) != set(ids):
                    raise InvalidTransition("one or more suggestion IDs do not exist")
                response_ids = {row["response_id"] for row in by_id.values()}
                if len(response_ids) != 1:
                    raise InvalidTransition("selected suggestions must come from one response")
                if response_ids != {current_response_id}:
                    raise InvalidTransition(
                        "suggestions must belong to the current assistant response"
                    )
                if any(row["status"] != "shown" for row in by_id.values()):
                    raise InvalidTransition("one or more suggestions were already resolved")
                context["suggestions"] = [by_id[item] for item in ids]

            response_id = event.payload.get("response_id")
            if response_id:
                historical_questionnaire = event.kind == "question_ideas_requested"
                if response_id != current_response_id and not historical_questionnaire:
                    raise InvalidTransition(
                        "the addressed assistant response is no longer current"
                    )
                response = db.execute(
                    "SELECT * FROM assistant_responses WHERE id = ? AND session_id = ?",
                    (response_id, event.session_id),
                ).fetchone()
                if not response:
                    raise InvalidTransition("the addressed assistant response does not exist")
                context["response"] = dict(response)
                if (
                    event.kind == "reflection_confirmed"
                    and response["intent"] != "reflect_and_confirm"
                ):
                    raise InvalidTransition(
                        "only a reflection response can be confirmed"
                    )
                if event.kind == "question_skipped" and not response["question"].strip():
                    raise InvalidTransition(
                        "only a response containing a question can be skipped"
                    )
                if event.kind == "response_continued" and not (
                    response["intent"] == "coach_writer"
                    and not response["question"].strip()
                ):
                    raise InvalidTransition(
                        "only question-free coaching can be continued"
                    )

            if event.kind == "questionnaire_submitted":
                ids = [item["question_id"] for item in event.payload["answers"]]
                marks = ",".join("?" for _ in ids)
                rows = db.execute(
                    f"""SELECT id FROM interview_questions
                        WHERE session_id = ? AND response_id = ? AND id IN ({marks})""",
                    (event.session_id, current_response_id, *ids),
                ).fetchall()
                if {row["id"] for row in rows} != set(ids):
                    raise InvalidTransition(
                        "every answer must belong to the current question set"
                    )

            if event.kind == "questionnaire_revised":
                target = db.execute(
                    """SELECT * FROM interview_events
                       WHERE id = ? AND session_id = ? AND status = 'completed'
                         AND kind IN ('questionnaire_submitted', 'questionnaire_revised')""",
                    (event.payload["target_event_id"], event.session_id),
                ).fetchone()
                latest = db.execute(
                    """SELECT * FROM interview_events
                       WHERE session_id = ? AND status = 'completed'
                         AND kind IN ('questionnaire_submitted', 'questionnaire_revised')
                       ORDER BY sequence DESC LIMIT 1""",
                    (event.session_id,),
                ).fetchone()
                if not target or not latest or target["id"] != latest["id"]:
                    raise InvalidTransition("only the latest questionnaire answers can be revised")
                target_payload = json.loads(target["payload_json"])
                target_response_id = (
                    target_payload.get("response_id")
                    or target_payload.get("questionnaire_response_id")
                )
                response_id = event.payload["questionnaire_response_id"]
                if response_id != target_response_id:
                    raise InvalidTransition("the revision must address the original question set")
                ids = [item["question_id"] for item in event.payload["answers"]]
                marks = ",".join("?" for _ in ids)
                rows = db.execute(
                    f"""SELECT id FROM interview_questions
                        WHERE session_id = ? AND response_id = ? AND id IN ({marks})""",
                    (event.session_id, response_id, *ids),
                ).fetchall()
                if {row["id"] for row in rows} != set(ids):
                    raise InvalidTransition(
                        "every revised answer must belong to the original question set"
                    )
                context["target_event"] = dict(target)

            if event.kind == "question_ideas_requested":
                latest = db.execute(
                    """SELECT payload_json FROM interview_events
                       WHERE session_id = ? AND status = 'completed'
                         AND kind IN ('questionnaire_submitted', 'questionnaire_revised')
                       ORDER BY sequence DESC LIMIT 1""",
                    (event.session_id,),
                ).fetchone()
                latest_questionnaire_id = None
                if latest:
                    latest_payload = json.loads(latest["payload_json"])
                    latest_questionnaire_id = (
                        latest_payload.get("response_id")
                        or latest_payload.get("questionnaire_response_id")
                    )
                addressed_response_id = event.payload["response_id"]
                if addressed_response_id not in {current_response_id, latest_questionnaire_id}:
                    raise InvalidTransition(
                        "question help must address the active questionnaire"
                    )
                question = db.execute(
                    """SELECT id, response_id, text, explanation, focus
                       FROM interview_questions
                       WHERE id = ? AND session_id = ? AND response_id = ?""",
                    (
                        event.payload["question_id"], event.session_id,
                        addressed_response_id,
                    ),
                ).fetchone()
                if not question:
                    raise InvalidTransition(
                        "question help must address the active questionnaire"
                    )
                context["question"] = dict(question)

            if event.kind == "decision_revised":
                target = db.execute(
                    "SELECT * FROM interview_events WHERE id = ? AND session_id = ?",
                    (event.payload["target_event_id"], event.session_id),
                ).fetchone()
                if not target:
                    raise InvalidTransition("the decision being revised does not exist")
                context["target_event"] = dict(target)
            return context

    def publish(self, event: InterviewEvent, decision: TurnDecision,
                *, model_meta: dict[str, Any], reference_context: dict[str, Any]) -> dict[str, Any]:
        with self.transaction(immediate=True) as db:
            session = self._session_row(db, event.session_id)
            event_row = db.execute(
                "SELECT * FROM interview_events WHERE id = ?", (event.id,)
            ).fetchone()
            if not event_row:
                raise NotFound("event not found")
            if event_row["status"] == "completed" and event_row["result_json"]:
                return self._project_session(db, event.session_id)
            if session["revision"] != event.expected_revision:
                raise StaleRevision(session["revision"])

            response_id = _record_id("response")
            response_sequence = db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM assistant_responses WHERE session_id = ?",
                (event.session_id,),
            ).fetchone()[0]
            db.execute(
                """INSERT INTO assistant_responses
                   (id, session_id, event_id, sequence, intent, guidance, question,
                    focus, listening_for)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    response_id, event.session_id, event.id, response_sequence,
                    decision.intent, decision.guidance, decision.question,
                    decision.focus, decision.listening_for,
                ),
            )

            for position, item in enumerate(decision.suggestions, 1):
                db.execute(
                    """INSERT INTO suggestions
                       (id, session_id, response_id, position, label, detail)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        _record_id("suggestion"), event.session_id, response_id,
                        position, item.label, item.detail,
                    ),
                )

            for position, item in enumerate(decision.questions, 1):
                db.execute(
                    """INSERT INTO interview_questions
                       (id, session_id, response_id, position, text, explanation, focus)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _record_id("question"), event.session_id, response_id,
                        position, item.text, item.explanation, item.focus,
                    ),
                )

            self._apply_event_action(db, event, reference_context)
            # A new assistant response replaces the actionable response that came
            # before it. Any unresolved cards from older responses must therefore
            # stop being selectable; otherwise they remain valid in the database
            # after the UI has moved on and can be accepted out of context.
            if event.kind != "question_ideas_requested":
                db.execute(
                    """UPDATE suggestions SET status = 'superseded', resolved_event_id = ?,
                       resolved_at = CURRENT_TIMESTAMP
                       WHERE session_id = ? AND status = 'shown' AND response_id != ?""",
                    (event.id, event.session_id, response_id),
                )
            for fact in decision.facts:
                self._insert_fact(
                    db, event.session_id, fact.text, event.id, fact.evidence
                )
            for change in decision.gap_changes:
                if change.action == "open":
                    normalized = normalize_story_text(change.description)
                    db.execute(
                        """INSERT OR IGNORE INTO story_gaps
                           (id, session_id, description, normalized_description,
                            impact, opened_event_id, evidence)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            _record_id("gap"), event.session_id, change.description,
                            normalized, change.impact or "blocking", event.id,
                            change.evidence,
                        ),
                    )
                else:
                    db.execute(
                        """UPDATE story_gaps SET status = 'resolved',
                           resolved_event_id = ?, resolved_at = CURRENT_TIMESTAMP
                           WHERE id = ? AND session_id = ? AND status = 'open'""",
                        (event.id, change.gap_id, event.session_id),
                    )

            status = "active"
            score = float(session["readiness_score"])
            reason = session["readiness_reason"]
            if decision.readiness:
                score = decision.readiness.score
                reason = decision.readiness.reason
                blocking = db.execute(
                    """SELECT COUNT(*) FROM story_gaps
                       WHERE session_id = ? AND status = 'open' AND impact = 'blocking'""",
                    (event.session_id,),
                ).fetchone()[0]
                all_supported = score == 1.0
                if all_supported and not blocking and decision.readiness.recommend_outline:
                    status = "ready_for_outline"
                if event.kind == "continue_interview":
                    status = "active"
                lenses_json = {
                    name: {
                        "status": decision.readiness.lenses[name].status,
                        "evidence": decision.readiness.lenses[name].evidence,
                    }
                    for name in READINESS_LENSES
                }
                db.execute(
                    """INSERT INTO readiness_assessments
                       (id, session_id, event_id, response_id, score, lenses_json,
                        reason, recommend_outline, resulting_status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _record_id("readiness"), event.session_id, event.id,
                        response_id, score, _json(lenses_json), reason,
                        int(decision.readiness.recommend_outline), status,
                    ),
                )
            elif (
                session["status"] == "ready_for_outline"
                and event.kind not in {"continue_interview", "questionnaire_revised"}
            ):
                status = "ready_for_outline"
            if event.kind == "questionnaire_revised" and not decision.readiness:
                score = 0.0
                reason = "Answers changed; review the updated handoff before continuing."
                lenses_json = {
                    name: {"status": "uncertain", "evidence": ""}
                    for name in READINESS_LENSES
                }
                db.execute(
                    """INSERT INTO readiness_assessments
                       (id, session_id, event_id, response_id, score, lenses_json,
                        reason, recommend_outline, resulting_status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'active')""",
                    (
                        _record_id("readiness"), event.session_id, event.id,
                        response_id, score, _json(lenses_json), reason,
                    ),
                )
            if event.kind == "interview_finished":
                status = "ready_for_outline"

            new_revision = session["revision"] + 1
            current_response_id = (
                session["current_response_id"]
                if event.kind == "question_ideas_requested"
                else response_id
            )
            question_target = (
                len(decision.questions)
                if event.kind == "interview_started"
                else session["question_target"]
            )
            db.execute(
                """UPDATE interview_sessions
                   SET revision = ?, status = ?, readiness_score = ?,
                       readiness_reason = ?, current_response_id = ?, question_target = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (
                    new_revision, status, score, reason, current_response_id,
                    question_target,
                    event.session_id,
                ),
            )
            db.execute(
                """UPDATE agent_runs SET status = 'completed', model_attempts = ?,
                   backend = ?, model = ?, prompt_version = ?, prompt_tokens = ?,
                   output_tokens = ?, safe_error = ?, finished_at = CURRENT_TIMESTAMP
                   WHERE event_id = ?""",
                (
                    int(model_meta.get("attempts", 0)), str(model_meta.get("backend", "")),
                    str(model_meta.get("model", "")), str(model_meta.get("prompt_version", "")),
                    int(model_meta.get("prompt_tokens", 0)),
                    int(model_meta.get("output_tokens", 0)),
                    _agent_diagnostic(model_meta), event.id,
                ),
            )
            db.execute(
                """UPDATE interview_events SET status = 'completed', result_revision = ?,
                   safe_error = '', completed_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (new_revision, event.id),
            )
            result = self._project_session(db, event.session_id)
            db.execute(
                "UPDATE interview_events SET result_json = ? WHERE id = ?",
                (_json(result), event.id),
            )
            return result

    def _apply_event_action(self, db: sqlite3.Connection, event: InterviewEvent,
                            context: dict[str, Any]) -> None:
        if event.kind == "interview_started":
            self._insert_fact(
                db, event.session_id, event.payload["seed"], event.id,
                event.payload["seed"],
            )
        elif event.kind in {"suggestions_selected", "suggestions_rejected"}:
            status = "selected" if event.kind == "suggestions_selected" else "rejected"
            for suggestion in context.get("suggestions", []):
                db.execute(
                    """UPDATE suggestions SET status = ?, resolved_event_id = ?,
                       writer_note = ?, resolved_at = CURRENT_TIMESTAMP
                       WHERE id = ? AND status = 'shown'""",
                    (status, event.id, event.payload.get("note", ""), suggestion["id"]),
                )
                if status == "selected" and not event.payload.get("note"):
                    self._insert_fact(
                        db, event.session_id,
                        f"{suggestion['label']}: {suggestion['detail']}", event.id,
                        f"Selected suggestion: {suggestion['label']}",
                        source_suggestion_id=suggestion["id"],
                    )
            if status == "selected" and event.payload.get("note"):
                self._insert_fact(
                    db, event.session_id, event.payload["note"], event.id,
                    event.payload["note"],
                )
            if context.get("suggestions"):
                response_id = context["suggestions"][0]["response_id"]
                db.execute(
                    """UPDATE suggestions SET status = 'superseded', resolved_event_id = ?,
                       resolved_at = CURRENT_TIMESTAMP
                       WHERE response_id = ? AND status = 'shown'""",
                    (event.id, response_id),
                )
        elif event.kind == "decision_revised":
            db.execute(
                """UPDATE story_facts SET status = 'superseded', ended_at = CURRENT_TIMESTAMP
                   WHERE session_id = ? AND source_event_id = ? AND status = 'active'""",
                (event.session_id, event.payload["target_event_id"]),
            )
            self._insert_fact(
                db, event.session_id, event.payload["replacement_text"], event.id,
                event.payload["replacement_text"],
            )
        elif event.kind == "questionnaire_revised":
            target_event_id = event.payload["target_event_id"]
            db.execute(
                """UPDATE story_facts SET status = 'superseded', ended_at = CURRENT_TIMESTAMP
                   WHERE session_id = ? AND source_event_id = ? AND status = 'active'""",
                (event.session_id, target_event_id),
            )
            db.execute(
                """UPDATE story_gaps SET status = 'resolved', resolved_event_id = ?,
                   resolved_at = CURRENT_TIMESTAMP
                   WHERE session_id = ? AND opened_event_id = ? AND status = 'open'""",
                (event.id, event.session_id, target_event_id),
            )

    def _insert_fact(self, db: sqlite3.Connection, session_id: str, text: str,
                     source_event_id: str, evidence: str,
                     *, source_suggestion_id: str | None = None) -> None:
        normalized = normalize_story_text(text)
        if not normalized:
            return
        db.execute(
            """INSERT OR IGNORE INTO story_facts
               (id, session_id, text, normalized_text, source_event_id,
                source_suggestion_id, evidence)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                _record_id("fact"), session_id, text, normalized,
                source_event_id, source_suggestion_id, evidence,
            ),
        )

    def mark_failed(self, event_id: str, error: str, *, attempts: int = 0,
                    backend: str = "", model: str = "") -> None:
        safe = error.strip()[:500]
        with self.transaction(immediate=True) as db:
            db.execute(
                """UPDATE interview_events SET status = 'failed_retryable', safe_error = ?
                   WHERE id = ? AND status IN ('pending', 'processing')""",
                (safe, event_id),
            )
            db.execute(
                """UPDATE agent_runs SET status = 'failed', model_attempts = ?,
                   backend = ?, model = ?, safe_error = ?, finished_at = CURRENT_TIMESTAMP
                   WHERE event_id = ?""",
                (attempts, backend, model, safe, event_id),
            )

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self.connect() as db:
            self._session_row(db, session_id)
            return self._project_session(db, session_id)

    def list_sessions(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT s.*,
                          (SELECT question FROM assistant_responses r
                           WHERE r.session_id = s.id ORDER BY r.sequence DESC LIMIT 1)
                          AS current_question
                   FROM interview_sessions s
                   ORDER BY s.updated_at DESC, s.rowid DESC"""
            ).fetchall()
            return [dict(row) for row in rows]

    def counts(self, session_id: str) -> dict[str, int]:
        """Diagnostic helper used to prove read-only behavior in tests."""
        names = (
            "interview_events", "assistant_responses", "interview_questions", "suggestions",
            "story_facts", "story_gaps", "readiness_assessments", "agent_runs",
        )
        with self.connect() as db:
            return {
                name: db.execute(
                    f"SELECT COUNT(*) FROM {name} WHERE session_id = ?", (session_id,)
                ).fetchone()[0]
                for name in names
            }

    def _session_row(self, db: sqlite3.Connection, session_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM interview_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            raise NotFound("Interview not found")
        return row

    def _project_session(self, db: sqlite3.Connection, session_id: str) -> dict[str, Any]:
        session = dict(self._session_row(db, session_id))
        rows = db.execute(
            """SELECT e.*, r.id AS response_id, r.intent, r.guidance, r.question,
                      r.focus, r.listening_for, r.created_at AS response_created_at,
                      a.model_attempts, a.backend AS run_backend,
                      a.model AS run_model, a.safe_error AS run_diagnostic
               FROM interview_events e
               LEFT JOIN assistant_responses r ON r.event_id = e.id
               LEFT JOIN agent_runs a ON a.event_id = e.id
               WHERE e.session_id = ? ORDER BY e.sequence""",
            (session_id,),
        ).fetchall()
        timeline: list[dict[str, Any]] = []
        for row in rows:
            event = {
                "id": row["id"],
                "sequence": row["sequence"],
                "expected_revision": row["expected_revision"],
                "kind": row["kind"],
                "payload": json.loads(row["payload_json"]),
                "status": row["status"],
                "created_at": row["created_at"],
                "safe_error": row["safe_error"],
                "agent_run": {
                    "model_attempts": row["model_attempts"],
                    "backend": row["run_backend"],
                    "model": row["run_model"],
                    "diagnostic": _parse_diagnostic(row["run_diagnostic"]),
                },
            }
            response = None
            if row["response_id"]:
                response = {
                    "id": row["response_id"],
                    "intent": row["intent"],
                    "guidance": row["guidance"],
                    "question": row["question"],
                    "focus": row["focus"],
                    "listening_for": row["listening_for"],
                    "created_at": row["response_created_at"],
                    "suggestions": [dict(item) for item in db.execute(
                        """SELECT id, label, detail, status, writer_note
                           FROM suggestions WHERE response_id = ? ORDER BY position""",
                        (row["response_id"],),
                    )],
                    "questions": [dict(item) for item in db.execute(
                        """SELECT id, position, text, explanation, focus
                           FROM interview_questions WHERE response_id = ? ORDER BY position""",
                        (row["response_id"],),
                    )],
                }
            timeline.append({"event": event, "response": response})
        facts = [dict(row) for row in db.execute(
            """SELECT id, text, evidence, source_event_id, source_suggestion_id,
                      created_at FROM story_facts
               WHERE session_id = ? AND status = 'active' ORDER BY created_at, rowid""",
            (session_id,),
        )]
        gaps = [dict(row) for row in db.execute(
            """SELECT id, description, impact, evidence, created_at FROM story_gaps
               WHERE session_id = ? AND status = 'open' ORDER BY created_at, rowid""",
            (session_id,),
        )]
        readiness_row = db.execute(
            """SELECT score, lenses_json, reason, recommend_outline, resulting_status,
                      created_at FROM readiness_assessments
               WHERE session_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1""",
            (session_id,),
        ).fetchone()
        readiness = None
        if readiness_row:
            readiness = dict(readiness_row)
            readiness["lenses"] = json.loads(readiness.pop("lenses_json"))
            readiness["recommend_outline"] = bool(readiness["recommend_outline"])
        current = next(
            (
                item["response"] for item in timeline
                if item["response"]
                and item["response"]["id"] == session["current_response_id"]
            ),
            None,
        )
        return {
            "session": session,
            "timeline": timeline,
            "facts": facts,
            "gaps": gaps,
            "readiness": readiness,
            "current_response": current,
        }


def _agent_diagnostic(model_meta: dict[str, Any]) -> str:
    errors = list(model_meta.get("validation_errors") or [])
    dropped = list(model_meta.get("dropped_components") or [])
    if not errors and not dropped:
        return ""
    return _json({
        "used_fallback": bool(model_meta.get("used_fallback", False)),
        "dropped_components": dropped,
        "validation_errors": errors,
    })[:4000]


def _parse_diagnostic(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {"message": value}
    return parsed if isinstance(parsed, dict) else {"message": value}
