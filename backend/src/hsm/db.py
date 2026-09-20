"""SQLite connection and versioned empty-database initialization."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
from datetime import datetime, timezone


INITIAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def migrate(path: Path) -> None:
    """Create the initial schema once; safe to run repeatedly on an empty DB."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as connection:
        with connection:
            connection.executescript(INITIAL_SCHEMA)
            row = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 1"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (1, datetime.now(timezone.utc).isoformat()),
                )
