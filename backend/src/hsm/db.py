"""SQLite connection handling and versioned control-plane migrations."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


Migration = tuple[int, tuple[str, ...]]


MIGRATIONS: tuple[Migration, ...] = (
    (1, ("""CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY, value_json TEXT NOT NULL
    )""",)),
    (2, (
        """CREATE TABLE users (
            id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, google_sub TEXT UNIQUE,
            name TEXT, role TEXT NOT NULL CHECK(role IN ('admin', 'user')),
            status TEXT NOT NULL CHECK(status IN ('invited', 'active', 'revoked')),
            quota_ram_bytes INTEGER NOT NULL CHECK(quota_ram_bytes >= 0),
            quota_cpu_cores INTEGER NOT NULL CHECK(quota_cpu_cores >= 0),
            quota_disk_bytes INTEGER NOT NULL CHECK(quota_disk_bytes >= 0),
            invited_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL, last_login_at TEXT
        )""",
        """CREATE TABLE sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, expires_at TEXT NOT NULL
        )""",
        """CREATE TABLE oauth_states (
            state_hash TEXT PRIMARY KEY, browser_binding_hash TEXT NOT NULL, nonce TEXT NOT NULL,
            pkce_verifier TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL
        )""",
        """CREATE TABLE containers (
            id TEXT PRIMARY KEY, project TEXT NOT NULL, lxd_uuid TEXT, current_name TEXT NOT NULL,
            owner_user_id INTEGER REFERENCES users(id), managed INTEGER NOT NULL CHECK(managed IN (0, 1)),
            isolation_status TEXT NOT NULL, lifecycle TEXT NOT NULL CHECK(lifecycle IN ('present', 'deleted')),
            first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, deleted_at TEXT,
            missing_complete_scans INTEGER NOT NULL DEFAULT 0 CHECK(missing_complete_scans >= 0)
        )""",
        """CREATE TABLE container_allocations (
            container_id TEXT PRIMARY KEY REFERENCES containers(id), ram_bytes INTEGER CHECK(ram_bytes >= 0),
            cpu_cores INTEGER CHECK(cpu_cores >= 0), cpu_allowance_pct INTEGER CHECK(cpu_allowance_pct BETWEEN 1 AND 100),
            disk_bytes INTEGER CHECK(disk_bytes >= 0), pool TEXT, config_fingerprint TEXT, verified_at TEXT NOT NULL
        )""",
        """CREATE TABLE container_assignments (
            user_id INTEGER NOT NULL REFERENCES users(id), container_id TEXT NOT NULL REFERENCES containers(id),
            assigned_by INTEGER NOT NULL REFERENCES users(id), assigned_at TEXT NOT NULL,
            PRIMARY KEY(user_id, container_id)
        )""",
        """CREATE TABLE metrics_latest (
            container_id TEXT PRIMARY KEY REFERENCES containers(id), state TEXT NOT NULL, image TEXT,
            os_version TEXT, ipv4_json TEXT NOT NULL, uptime_s INTEGER, processes INTEGER, cpu_pct REAL,
            cpu_core_equivalent REAL, ram_used_bytes INTEGER, disk_used_bytes INTEGER, net_rx_bytes INTEGER,
            net_tx_bytes INTEGER, net_rx_bps REAL, net_tx_bps REAL, sampled_at TEXT NOT NULL, error_code TEXT
        )""",
        """CREATE TABLE collector_status (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)""",
        """CREATE TABLE operations (
            id TEXT PRIMARY KEY, actor_user_id INTEGER REFERENCES users(id), kind TEXT NOT NULL,
            target_id TEXT REFERENCES containers(id), request_hash TEXT NOT NULL, idempotency_key TEXT,
            status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'succeeded', 'failed', 'unknown')),
            lxd_operation_id TEXT, bounded_result_json TEXT, error_code TEXT, created_at TEXT NOT NULL,
            deadline_at TEXT, completed_at TEXT
        )""",
        """CREATE TABLE allocation_reservations (
            operation_id TEXT NOT NULL REFERENCES operations(id), user_id INTEGER REFERENCES users(id),
            scope TEXT NOT NULL CHECK(scope IN ('user', 'host', 'pool')), scope_key TEXT NOT NULL,
            delta_ram_bytes INTEGER NOT NULL CHECK(delta_ram_bytes >= 0),
            delta_cpu_cores INTEGER NOT NULL CHECK(delta_cpu_cores >= 0),
            delta_disk_bytes INTEGER NOT NULL CHECK(delta_disk_bytes >= 0),
            PRIMARY KEY(operation_id, scope, scope_key)
        )""",
        """CREATE TABLE history_jobs (
            id TEXT PRIMARY KEY, actor_user_id INTEGER NOT NULL REFERENCES users(id),
            kind TEXT NOT NULL CHECK(kind IN ('history', 'accounting')), container_id TEXT REFERENCES containers(id),
            range_start TEXT NOT NULL, range_end TEXT NOT NULL,
            bucket_count INTEGER NOT NULL CHECK(bucket_count BETWEEN 1 AND 300), status TEXT NOT NULL,
            cursor_json TEXT, bounded_result_json TEXT, error_code TEXT, created_at TEXT NOT NULL, expires_at TEXT NOT NULL
        )""",
        """CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY, ts TEXT NOT NULL, actor_user_id INTEGER REFERENCES users(id), actor_email TEXT,
            action TEXT NOT NULL, target_type TEXT NOT NULL, target_id TEXT, target_name TEXT,
            operation_id TEXT REFERENCES operations(id), detail_json TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK(outcome IN ('intent', 'ok', 'denied', 'error', 'unknown'))
        )""",
        "CREATE INDEX sessions_user_expiry_idx ON sessions(user_id, expires_at)",
        "CREATE INDEX oauth_states_expiry_idx ON oauth_states(expires_at)",
        "CREATE INDEX assignments_container_idx ON container_assignments(container_id)",
        "CREATE INDEX containers_project_name_idx ON containers(project, current_name)",
        "CREATE UNIQUE INDEX containers_project_lxd_uuid_idx ON containers(project, lxd_uuid) WHERE lxd_uuid IS NOT NULL",
        "CREATE INDEX operations_status_created_idx ON operations(status, created_at)",
        "CREATE UNIQUE INDEX operations_actor_idempotency_idx ON operations(actor_user_id, idempotency_key) WHERE idempotency_key IS NOT NULL",
        "CREATE INDEX history_jobs_status_created_idx ON history_jobs(status, created_at)",
        "CREATE INDEX audit_log_ts_idx ON audit_log(ts)",
        "CREATE INDEX audit_log_target_ts_idx ON audit_log(target_id, ts)",
    )),
    (3, ("""CREATE TABLE metrics_history (
        container_id TEXT NOT NULL REFERENCES containers(id), sampled_at TEXT NOT NULL,
        state TEXT NOT NULL, cpu_pct REAL, ram_used_bytes INTEGER, disk_used_bytes INTEGER,
        net_rx_bytes INTEGER, net_tx_bytes INTEGER, PRIMARY KEY(container_id, sampled_at)
    )""", "CREATE INDEX metrics_history_container_time_idx ON metrics_history(container_id, sampled_at)")),
    (4, ("""CREATE TABLE execution_slots (
        operation_id TEXT PRIMARY KEY REFERENCES operations(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id), acquired_at TEXT NOT NULL, deadline_at TEXT NOT NULL
    )""", "CREATE UNIQUE INDEX execution_slots_user_idx ON execution_slots(user_id)")),
)


def connect(path: Path) -> sqlite3.Connection:
    """Open one SQLite connection; callers must not share it across threads."""
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def migrate(path: Path) -> None:
    """Apply pending migrations atomically, in ascending version order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
        for version, statements in MIGRATIONS:
            if version in applied:
                continue
            connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in statements:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, datetime.now(timezone.utc).isoformat()),
                )
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()
