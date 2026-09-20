from pathlib import Path
import tempfile
import unittest

from hsm.db import connect, migrate


class MigrationTests(unittest.TestCase):
    def test_initialization_is_idempotent_and_enables_foreign_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "hsm.sqlite"
            migrate(database)
            migrate(database)
            connection = connect(database)
            try:
                versions = connection.execute("SELECT version FROM schema_migrations").fetchall()
                foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
            finally:
                connection.close()
        self.assertEqual(versions, [(1,)])
        self.assertEqual(foreign_keys, 1)
