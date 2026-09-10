from __future__ import annotations

import sqlite3
from pathlib import Path
import tempfile
import unittest

from second_unit.agent import StoryEditor
from second_unit.database import Repository, SCHEMA_VERSION
from second_unit.model import NoModel
from second_unit.domain import make_start_event
from second_unit.service import InterviewService


class RepositoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "interview.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def repository(self) -> Repository:
        return Repository(self.database_path)

    def service(self, repository: Repository | None = None) -> InterviewService:
        return InterviewService(
            repository or self.repository(),
            StoryEditor(NoModel()),
        )

    def create(self, service: InterviewService | None = None) -> dict:
        return (service or self.service()).create_interview({
            "event_id": "event_start_0001",
            "title": "Last Train",
            "seed": "Mara misses the last train home.",
            "storytelling_format": "hybrid",
            "involvement_mode": "collaborative",
        })


class MigrationTests(RepositoryTestCase):
    def test_migration_is_repeatable_and_recorded_once(self) -> None:
        repository = self.repository()
        repository.migrate()
        Repository(self.database_path).migrate()

        with repository.connect() as db:
            versions = db.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }

        self.assertEqual([row[0] for row in versions], [SCHEMA_VERSION])
        self.assertTrue({
            "interview_sessions",
            "interview_events",
            "assistant_responses",
            "interview_questions",
            "suggestions",
            "story_facts",
            "story_gaps",
            "readiness_assessments",
            "agent_runs",
            "outlines",
            "outline_structures",
            "outline_beats",
            "outline_approvals",
            "outline_versions",
        } <= tables)

    def test_version_one_database_gains_question_target_without_losing_sessions(self) -> None:
        with sqlite3.connect(self.database_path) as db:
            db.executescript("""
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO schema_migrations(version) VALUES (1);
                CREATE TABLE interview_sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    seed TEXT NOT NULL,
                    storytelling_format TEXT NOT NULL,
                    involvement_mode TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    revision INTEGER NOT NULL DEFAULT 0,
                    readiness_score REAL NOT NULL DEFAULT 0,
                    readiness_reason TEXT NOT NULL DEFAULT 'Not assessed.',
                    current_response_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO interview_sessions
                    (id, title, seed, storytelling_format, involvement_mode)
                VALUES
                    ('interview_legacy_0001', 'Legacy', 'A room.', 'not_sure', 'collaborative');
            """)

        repository = Repository(self.database_path)
        with repository.connect() as db:
            row = db.execute(
                "SELECT title, question_target FROM interview_sessions WHERE id = ?",
                ("interview_legacy_0001",),
            ).fetchone()
            versions = db.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()

        self.assertEqual(dict(row), {"title": "Legacy", "question_target": 8})
        self.assertEqual([item[0] for item in versions], [SCHEMA_VERSION])

    def test_connections_enforce_foreign_keys(self) -> None:
        repository = self.repository()
        with repository.connect() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    """INSERT INTO interview_events
                       (id, session_id, sequence, expected_revision, kind, payload_json)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    ("event_orphan_01", "missing_session", 1, 0, "message_submitted", "{}"),
                )


class ProjectionPersistenceTests(RepositoryTestCase):
    def test_startup_recovery_marks_interrupted_turn_retryable(self) -> None:
        repository = self.repository()
        event = make_start_event(
            "interview_recovery_0001",
            "event_recovery_start_0001",
            seed="A man wakes in an empty cinema.",
            title="Empty Cinema",
            storytelling_format="not_sure",
            involvement_mode="collaborative",
        )
        repository.create_and_ingest_start(event)

        self.assertEqual(repository.recover_interrupted_runs(), 1)
        state = repository.get_session(event.session_id)
        self.assertEqual(state["timeline"][0]["event"]["status"], "failed_retryable")
        self.assertIn("saved", state["timeline"][0]["event"]["safe_error"])
        self.assertEqual(repository.recover_interrupted_runs(), 0)

    def test_create_then_reload_from_a_new_repository(self) -> None:
        created = self.create()
        session_id = created["session"]["id"]

        reopened = self.service(Repository(self.database_path)).get_interview(session_id)

        self.assertEqual(reopened["session"]["id"], session_id)
        self.assertEqual(reopened["session"]["revision"], 1)
        self.assertEqual(reopened["session"]["title"], "Last Train")
        self.assertEqual(reopened["session"]["seed"], "Mara misses the last train home.")
        self.assertEqual(len(reopened["timeline"]), 1)
        self.assertEqual(reopened["timeline"][0]["event"]["kind"], "interview_started")
        self.assertIsNotNone(reopened["current_response"])
        self.assertEqual(reopened["facts"][0]["text"], "Mara misses the last train home.")

    def test_get_projection_is_strictly_read_only(self) -> None:
        repository = self.repository()
        service = self.service(repository)
        created = self.create(service)
        session_id = created["session"]["id"]
        before_counts = repository.counts(session_id)
        before_revision = created["session"]["revision"]

        first = service.get_interview(session_id)
        second = service.get_interview(session_id)

        self.assertEqual(first, second)
        self.assertEqual(repository.counts(session_id), before_counts)
        self.assertEqual(second["session"]["revision"], before_revision)


if __name__ == "__main__":
    unittest.main()
