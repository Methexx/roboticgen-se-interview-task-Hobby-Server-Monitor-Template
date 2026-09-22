from pathlib import Path
import tempfile
import unittest

import falcon.testing

from hsm.app import create_app
from hsm.auth.sessions import SessionService
from hsm.config import load_settings
from hsm.db import connect, migrate


NOW = "2026-09-22T00:00:00+00:00"


class UsersApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "hsm.sqlite"
        migrate(self.database)
        connection = connect(self.database)
        try:
            with connection:
                connection.executemany(
                    """INSERT INTO users (
                        id, email, role, status, quota_ram_bytes, quota_cpu_cores, quota_disk_bytes, created_at
                    ) VALUES (?, ?, ?, 'active', 8, 4, 16, ?)""",
                    [(1, "admin@example.test", "admin", NOW), (2, "user@example.test", "user", NOW)],
                )
                connection.execute(
                    """INSERT INTO containers (
                        id, project, current_name, owner_user_id, managed, isolation_status, lifecycle,
                        first_seen_at, last_seen_at
                    ) VALUES ('container-a', 'hsm', 'container-a', 1, 1, 'safe', 'present', ?, ?)""",
                    (NOW, NOW),
                )
                connection.execute(
                    """INSERT INTO container_allocations (
                        container_id, ram_bytes, cpu_cores, disk_bytes, verified_at
                    ) VALUES ('container-a', 1, 1, 1, ?)""",
                    (NOW,),
                )
        finally:
            connection.close()
        self.settings = load_settings({
            "PUBLIC_BASE_URL": "http://localhost:8000", "GOOGLE_CLIENT_ID": "test-client",
            "GOOGLE_CLIENT_SECRET": "test-secret", "BOOTSTRAP_ADMIN_EMAIL": "admin@example.test",
            "COOKIE_SECURE": "false", "DATABASE_PATH": str(self.database),
        })
        self.client = falcon.testing.TestClient(create_app(self.settings))
        sessions = SessionService(self.database, idle_seconds=1800, absolute_seconds=28800)
        self.admin_token = sessions.create(1)
        self.user_token = sessions.create(2)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_users_are_admin_only(self) -> None:
        self.assertEqual(self.client.simulate_get("/api/users").status_code, 401)
        self.assertEqual(self.client.simulate_get("/api/users", cookies={"hsm_session": self.user_token}).status_code, 403)
        response = self.client.simulate_get("/api/users", cookies={"hsm_session": self.admin_token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["email"] for item in response.json["items"]], ["admin@example.test", "user@example.test"])

    def test_admin_can_invite_and_unknown_fields_are_rejected(self) -> None:
        headers = {"Content-Type": "application/json"}
        response = self.client.simulate_post(
            "/api/users", cookies={"hsm_session": self.admin_token}, headers=headers,
            json={"email": "invitee@example.test", "role": "user", "quota": {"ram_bytes": 2, "cpu_cores": 1, "disk_bytes": 4}},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["status"], "invited")
        response = self.client.simulate_post(
            "/api/users", cookies={"hsm_session": self.admin_token}, headers=headers,
            json={"email": "bad@example.test", "role": "user", "quota": {"ram_bytes": 2, "cpu_cores": 1, "disk_bytes": 4}, "extra": True},
        )
        self.assertEqual(response.status_code, 400)

    def test_last_admin_protection_and_revocation_use_http_statuses(self) -> None:
        response = self.client.simulate_patch(
            "/api/users/1", cookies={"hsm_session": self.admin_token}, headers={"Content-Type": "application/json"},
            json={"role": "user"},
        )
        self.assertEqual(response.status_code, 409)
        response = self.client.simulate_delete("/api/users/2", cookies={"hsm_session": self.admin_token})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.client.simulate_get("/api/users", cookies={"hsm_session": self.user_token}).status_code, 401)

    def test_assignment_and_revocation_are_admin_routes(self) -> None:
        headers = {"Content-Type": "application/json"}
        response = self.client.simulate_put(
            "/api/users/2/containers/container-a", cookies={"hsm_session": self.admin_token},
            headers=headers, json={},
        )
        self.assertEqual(response.status_code, 204)
        connection = connect(self.database)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM container_assignments").fetchone()[0], 1)
        finally:
            connection.close()
        response = self.client.simulate_delete(
            "/api/users/2/containers/container-a", cookies={"hsm_session": self.admin_token}
        )
        self.assertEqual(response.status_code, 204)
