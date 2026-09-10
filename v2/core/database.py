"""SQLite schema and connection helpers for the Second Unit production harness.

The database is the canonical index for production state. Large artifact bodies
(screenplays, generated PDFs, images) may later live in object/file storage;
their immutable versions and provenance still belong here.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_VERSION = 1


MIGRATIONS: tuple[str, ...] = (
    """
    PRAGMA foreign_keys = ON;

    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active'
            CHECK (status IN ('active', 'on_hold', 'completed', 'archived')),
        phase TEXT NOT NULL DEFAULT 'development'
            CHECK (phase IN ('development', 'screenplay', 'preproduction',
                             'production', 'postproduction', 'completed')),
        timezone TEXT NOT NULL DEFAULT 'UTC',
        target_runtime_minutes INTEGER,
        shoot_start_date TEXT,
        delivery_date TEXT,
        assistant_mode TEXT NOT NULL DEFAULT 'propose'
            CHECK (assistant_mode IN ('observe', 'propose', 'auto_low_risk')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS project_settings (
        project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
        creative_involvement INTEGER NOT NULL DEFAULT 75
            CHECK (creative_involvement BETWEEN 0 AND 100),
        reminder_policy_json TEXT NOT NULL DEFAULT '{}',
        permission_policy_json TEXT NOT NULL DEFAULT '{}',
        budget_limit_cents INTEGER CHECK (budget_limit_cents >= 0),
        working_hours_json TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS interview_sessions (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        seed TEXT NOT NULL DEFAULT '',
        storytelling_format TEXT,
        involvement INTEGER NOT NULL DEFAULT 75 CHECK (involvement BETWEEN 0 AND 100),
        readiness REAL NOT NULL DEFAULT 0 CHECK (readiness BETWEEN 0 AND 1),
        readiness_reason TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active'
            CHECK (status IN ('active', 'complete', 'archived')),
        started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        completed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS interview_turns (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
        sequence INTEGER NOT NULL,
        question TEXT NOT NULL,
        answer TEXT,
        focus TEXT,
        skipped INTEGER NOT NULL DEFAULT 0 CHECK (skipped IN (0, 1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        answered_at TEXT,
        UNIQUE (session_id, sequence)
    );

    CREATE TABLE IF NOT EXISTS artifacts (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'working'
            CHECK (status IN ('working', 'in_review', 'approved', 'superseded', 'archived')),
        current_version_id TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS artifact_versions (
        id TEXT PRIMARY KEY,
        artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
        version INTEGER NOT NULL CHECK (version > 0),
        body_json TEXT,
        storage_uri TEXT,
        content_hash TEXT NOT NULL,
        source TEXT NOT NULL CHECK (source IN ('human', 'assistant', 'import', 'system')),
        change_summary TEXT NOT NULL DEFAULT '',
        created_by TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (body_json IS NOT NULL OR storage_uri IS NOT NULL),
        UNIQUE (artifact_id, version),
        UNIQUE (artifact_id, content_hash)
    );

    CREATE TABLE IF NOT EXISTS artifact_dependencies (
        artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
        depends_on_artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
        consumed_version_id TEXT REFERENCES artifact_versions(id),
        stale INTEGER NOT NULL DEFAULT 0 CHECK (stale IN (0, 1)),
        reason TEXT,
        PRIMARY KEY (artifact_id, depends_on_artifact_id),
        CHECK (artifact_id <> depends_on_artifact_id)
    );

    CREATE TABLE IF NOT EXISTS scenes (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        screenplay_version_id TEXT REFERENCES artifact_versions(id),
        scene_number TEXT NOT NULL,
        slugline TEXT NOT NULL,
        interior_exterior TEXT CHECK (interior_exterior IN ('INT', 'EXT', 'INT/EXT', 'EXT/INT')),
        time_of_day TEXT,
        synopsis TEXT NOT NULL DEFAULT '',
        page_eighths INTEGER CHECK (page_eighths BETWEEN 0 AND 80),
        estimated_minutes REAL CHECK (estimated_minutes >= 0),
        status TEXT NOT NULL DEFAULT 'planned'
            CHECK (status IN ('planned', 'scheduled', 'shot', 'omitted')),
        UNIQUE (project_id, scene_number)
    );

    CREATE TABLE IF NOT EXISTS production_elements (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        category TEXT NOT NULL,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        confirmation_status TEXT NOT NULL DEFAULT 'suggested'
            CHECK (confirmation_status IN ('suggested', 'confirmed', 'rejected')),
        UNIQUE (project_id, category, name)
    );

    CREATE TABLE IF NOT EXISTS scene_elements (
        scene_id TEXT NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
        element_id TEXT NOT NULL REFERENCES production_elements(id) ON DELETE CASCADE,
        notes TEXT NOT NULL DEFAULT '',
        source_excerpt TEXT,
        PRIMARY KEY (scene_id, element_id)
    );

    CREATE TABLE IF NOT EXISTS shoot_days (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        shoot_date TEXT NOT NULL,
        unit_name TEXT NOT NULL DEFAULT 'Main Unit',
        location_name TEXT,
        call_time TEXT,
        estimated_wrap_time TEXT,
        status TEXT NOT NULL DEFAULT 'draft'
            CHECK (status IN ('draft', 'approved', 'in_progress', 'completed', 'cancelled')),
        readiness_status TEXT NOT NULL DEFAULT 'unknown'
            CHECK (readiness_status IN ('unknown', 'ready', 'at_risk', 'blocked')),
        notes TEXT NOT NULL DEFAULT '',
        UNIQUE (project_id, shoot_date, unit_name)
    );

    CREATE TABLE IF NOT EXISTS scheduled_scenes (
        shoot_day_id TEXT NOT NULL REFERENCES shoot_days(id) ON DELETE CASCADE,
        scene_id TEXT NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
        sequence INTEGER NOT NULL,
        scheduled_minutes INTEGER CHECK (scheduled_minutes >= 0),
        status TEXT NOT NULL DEFAULT 'scheduled'
            CHECK (status IN ('scheduled', 'in_progress', 'completed', 'dropped')),
        PRIMARY KEY (shoot_day_id, scene_id),
        UNIQUE (shoot_day_id, sequence)
    );

    CREATE TABLE IF NOT EXISTS shots (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        scene_id TEXT NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
        shot_number TEXT NOT NULL,
        framing TEXT,
        angle TEXT,
        movement TEXT,
        description TEXT NOT NULL,
        lens TEXT,
        equipment TEXT,
        estimated_setup_minutes INTEGER CHECK (estimated_setup_minutes >= 0),
        priority TEXT NOT NULL DEFAULT 'essential'
            CHECK (priority IN ('essential', 'useful', 'optional')),
        status TEXT NOT NULL DEFAULT 'planned'
            CHECK (status IN ('planned', 'ready', 'shot', 'dropped')),
        coverage_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE (scene_id, shot_number)
    );

    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        shoot_day_id TEXT REFERENCES shoot_days(id) ON DELETE SET NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        owner TEXT,
        due_at TEXT,
        status TEXT NOT NULL DEFAULT 'suggested'
            CHECK (status IN ('suggested', 'todo', 'in_progress', 'blocked', 'done', 'cancelled')),
        priority TEXT NOT NULL DEFAULT 'normal'
            CHECK (priority IN ('low', 'normal', 'high', 'critical')),
        requires_human INTEGER NOT NULL DEFAULT 1 CHECK (requires_human IN (0, 1)),
        source TEXT NOT NULL DEFAULT 'human'
            CHECK (source IN ('human', 'assistant', 'system')),
        completed_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS task_dependencies (
        task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        depends_on_task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        PRIMARY KEY (task_id, depends_on_task_id),
        CHECK (task_id <> depends_on_task_id)
    );

    CREATE TABLE IF NOT EXISTS task_links (
        task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        relation TEXT NOT NULL DEFAULT 'affects',
        PRIMARY KEY (task_id, entity_type, entity_id, relation)
    );

    CREATE TABLE IF NOT EXISTS approvals (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        entity_version_id TEXT,
        decision TEXT NOT NULL CHECK (decision IN ('pending', 'approved', 'rejected', 'changes_requested')),
        requested_by TEXT NOT NULL,
        decided_by TEXT,
        note TEXT NOT NULL DEFAULT '',
        requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        decided_at TEXT
    );

    CREATE TABLE IF NOT EXISTS assistant_proposals (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        proposal_type TEXT NOT NULL,
        title TEXT NOT NULL,
        rationale TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        impact_json TEXT NOT NULL DEFAULT '{}',
        risk_level TEXT NOT NULL DEFAULT 'low'
            CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'accepted', 'modified', 'rejected', 'expired')),
        expires_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        resolved_at TEXT
    );

    CREATE TABLE IF NOT EXISTS agent_runs (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        trigger_type TEXT NOT NULL,
        trigger_payload_json TEXT NOT NULL DEFAULT '{}',
        workflow TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'running'
            CHECK (status IN ('running', 'waiting_approval', 'completed', 'failed', 'cancelled')),
        input_snapshot_json TEXT NOT NULL DEFAULT '{}',
        output_summary_json TEXT NOT NULL DEFAULT '{}',
        cost_cents INTEGER NOT NULL DEFAULT 0 CHECK (cost_cents >= 0),
        started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        finished_at TEXT
    );

    CREATE TABLE IF NOT EXISTS activity_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        actor_type TEXT NOT NULL CHECK (actor_type IN ('human', 'assistant', 'system')),
        actor_id TEXT,
        event_type TEXT NOT NULL,
        entity_type TEXT,
        entity_id TEXT,
        payload_json TEXT NOT NULL DEFAULT '{}',
        occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(project_id, status, due_at);
    CREATE INDEX IF NOT EXISTS idx_shoot_days_date ON shoot_days(project_id, shoot_date);
    CREATE INDEX IF NOT EXISTS idx_events_project_time ON activity_events(project_id, occurred_at);
    CREATE INDEX IF NOT EXISTS idx_proposals_pending ON assistant_proposals(project_id, status);
    CREATE INDEX IF NOT EXISTS idx_artifacts_project_kind ON artifacts(project_id, kind);
    """,
)


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a configured SQLite connection."""
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA busy_timeout = 5000")
    return db


def migrate(db: sqlite3.Connection) -> None:
    """Apply forward-only migrations. Safe to call whenever the app starts."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    applied = {row[0] for row in db.execute("SELECT version FROM schema_migrations")}
    for version, sql in enumerate(MIGRATIONS, start=1):
        if version in applied:
            continue
        with db:
            db.executescript(sql)
            db.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))


def initialize(path: str | Path) -> sqlite3.Connection:
    db = connect(path)
    migrate(db)
    return db
