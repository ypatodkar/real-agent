import sqlite3
import unittest

from core.database import SCHEMA_VERSION, migrate


class DatabaseSchemaTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("PRAGMA foreign_keys = ON")
        migrate(self.db)

    def tearDown(self):
        self.db.close()

    def test_schema_reaches_current_version(self):
        version = self.db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        self.assertEqual(version, SCHEMA_VERSION)

    def test_core_tables_exist(self):
        names = {
            row[0]
            for row in self.db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        self.assertTrue({"projects", "artifacts", "scenes", "shoot_days", "shots",
                         "tasks", "assistant_proposals", "activity_events"} <= names)

    def test_foreign_keys_reject_orphan_task(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO tasks(id, project_id, title) VALUES ('t1', 'missing', 'Task')"
            )

    def test_migration_is_idempotent(self):
        migrate(self.db)
        count = self.db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        self.assertEqual(count, SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
