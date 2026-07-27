from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class DatabaseStartupTests(unittest.TestCase):
    def setUp(self):
        import app.database as database

        self.database = database
        self.old_db_path = database.DB_PATH
        self.temporary_directory = tempfile.TemporaryDirectory()
        database.DB_PATH = Path(self.temporary_directory.name) / "startup.db"

    def tearDown(self):
        self.database.DB_PATH = self.old_db_path
        self.temporary_directory.cleanup()

    def test_second_startup_is_strictly_read_only(self):
        self.database.init_db()
        self.assertTrue(self.database.database_schema_is_current())

        with patch.object(
            self.database,
            "migrate_room_requests_budget_nullable",
            side_effect=AssertionError("current schema attempted a migration"),
        ) as migrate:
            self.database.init_db()

        migrate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
