from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from run import acquire_database_lock


class DatabaseProcessLockTests(unittest.TestCase):
    def test_only_one_process_handle_can_own_a_database_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = str(Path(temporary) / "interviews.db")
            first = acquire_database_lock(database)
            if first is None:
                self.skipTest("advisory file locking is unavailable on this platform")
            self.addCleanup(first.close)

            with self.assertRaises(SystemExit) as caught:
                acquire_database_lock(database)

            self.assertIn("already open", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
