from pathlib import Path
import sqlite3
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
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
            finally:
                connection.close()
        self.assertEqual(versions, [(1,), (2,), (3,)])
        self.assertEqual(foreign_keys, 1)
        self.assertTrue({
            "users", "sessions", "oauth_states", "containers", "container_assignments",
            "metrics_latest", "collector_status", "operations", "allocation_reservations",
            "history_jobs", "audit_log",
        }.issubset(tables))

    def test_upgrades_an_existing_v1_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "hsm.sqlite"
            connection = sqlite3.connect(database)
            connection.executescript("""
                CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
                CREATE TABLE app_settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
                INSERT INTO schema_migrations VALUES (1, '2026-09-20T00:00:00+00:00');
            """)
            connection.close()
            migrate(database)
            upgraded = connect(database)
            try:
                versions = upgraded.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
                has_users = upgraded.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
                ).fetchone()
            finally:
                upgraded.close()
        self.assertEqual(versions, [(1,), (2,), (3,)])
        self.assertEqual(has_users, (1,))
