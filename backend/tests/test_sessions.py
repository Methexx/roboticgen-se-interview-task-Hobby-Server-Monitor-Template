from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

import falcon
import falcon.testing

from hsm.auth.sessions import SessionService, token_hash
from hsm.app import RequestContextMiddleware
from hsm.authz.policy import Policy, PolicyRegistry
from hsm.db import connect, migrate


class SessionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "hsm.sqlite"
        migrate(self.database)
        connection = connect(self.database)
        try:
            with connection:
                connection.execute(
                    """INSERT INTO users (
                        id, email, role, status, quota_ram_bytes, quota_cpu_cores, quota_disk_bytes, created_at
                    ) VALUES (1, 'admin@example.test', 'admin', 'active', 1, 1, 1, ?)""",
                    (datetime.now(timezone.utc).isoformat(),),
                )
        finally:
            connection.close()
        self.sessions = SessionService(self.database, idle_seconds=60, absolute_seconds=300)
        self.now = datetime(2026, 9, 22, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_stores_only_a_hash_and_resolves_active_user(self) -> None:
        token = self.sessions.create(1, now=self.now)
        connection = connect(self.database)
        try:
            self.assertEqual(connection.execute("SELECT token_hash FROM sessions").fetchone()[0], token_hash(token))
        finally:
            connection.close()
        self.assertEqual(self.sessions.resolve(token, now=self.now + timedelta(seconds=30)).role, "admin")

    def test_revoked_user_session_fails_immediately(self) -> None:
        token = self.sessions.create(1, now=self.now)
        connection = connect(self.database)
        try:
            with connection:
                connection.execute("UPDATE users SET status = 'revoked' WHERE id = 1")
        finally:
            connection.close()
        self.assertIsNone(self.sessions.resolve(token, now=self.now + timedelta(seconds=1)))

    def test_idle_session_is_deleted(self) -> None:
        token = self.sessions.create(1, now=self.now)
        self.assertIsNone(self.sessions.resolve(token, now=self.now + timedelta(seconds=61)))
        connection = connect(self.database)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)
        finally:
            connection.close()

    def test_admin_policy_rejects_anonymous_and_non_admin_sessions(self) -> None:
        connection = connect(self.database)
        try:
            with connection:
                connection.execute(
                    """INSERT INTO users (
                        id, email, role, status, quota_ram_bytes, quota_cpu_cores, quota_disk_bytes, created_at
                    ) VALUES (2, 'user@example.test', 'user', 'active', 1, 1, 1, ?)""",
                    (self.now.isoformat(),),
                )
        finally:
            connection.close()
        token = self.sessions.create(2, now=self.now)
        registry = PolicyRegistry()
        app = falcon.App(middleware=[RequestContextMiddleware(registry, self.sessions)])
        registry.add_route(app, "/admin", _AdminResource(), {"GET": Policy.ADMIN})
        client = falcon.testing.TestClient(app)
        self.assertEqual(client.simulate_get("/admin").status_code, 401)
        self.assertEqual(client.simulate_get("/admin", cookies={"hsm_session": token}).status_code, 403)


class _AdminResource:
    def on_get(self, request: falcon.Request, response: falcon.Response) -> None:
        response.media = {"ok": True}
