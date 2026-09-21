from pathlib import Path
import tempfile
import unittest

import falcon.testing

from hsm.app import create_app
from hsm.auth.sessions import SessionService
from hsm.config import load_settings
from hsm.db import connect, migrate


NOW = "2026-09-22T00:00:00+00:00"


class ContainersApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        database = Path(self.directory.name) / "hsm.sqlite"
        migrate(database)
        connection = connect(database)
        try:
            with connection:
                connection.executemany(
                    """INSERT INTO users (
                        id, email, role, status, quota_ram_bytes, quota_cpu_cores, quota_disk_bytes, created_at
                    ) VALUES (?, ?, ?, 'active', 8, 4, 16, ?)""",
                    [(1, "admin@example.test", "admin", NOW), (2, "user@example.test", "user", NOW)],
                )
                for identifier in ("container-a", "container-b"):
                    connection.execute(
                        """INSERT INTO containers (
                            id, project, current_name, managed, isolation_status, lifecycle, first_seen_at, last_seen_at
                        ) VALUES (?, 'hsm', ?, 1, 'safe', 'present', ?, ?)""",
                        (identifier, identifier, NOW, NOW),
                    )
                connection.execute(
                    "INSERT INTO container_assignments(user_id, container_id, assigned_by, assigned_at) VALUES (2, 'container-a', 1, ?)",
                    (NOW,),
                )
                connection.execute(
                    """INSERT INTO metrics_latest(container_id, state, ipv4_json, sampled_at)
                       VALUES ('container-a', 'Running', '["10.70.0.2"]', ?)""",
                    (NOW,),
                )
        finally:
            connection.close()
        settings = load_settings({
            "PUBLIC_BASE_URL": "http://localhost:8000", "GOOGLE_CLIENT_ID": "test-client",
            "GOOGLE_CLIENT_SECRET": "test-secret", "BOOTSTRAP_ADMIN_EMAIL": "admin@example.test",
            "COOKIE_SECURE": "false", "DATABASE_PATH": str(database),
        })
        self.client = falcon.testing.TestClient(create_app(settings))
        sessions = SessionService(database, idle_seconds=1800, absolute_seconds=28800)
        self.admin_token = sessions.create(1)
        self.user_token = sessions.create(2)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_container_user_lists_only_explicit_assignments(self) -> None:
        response = self.client.simulate_get("/api/containers", cookies={"hsm_session": self.user_token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json["items"]], ["container-a"])

    def test_existing_unassigned_container_is_403_before_read(self) -> None:
        response = self.client.simulate_get("/api/containers/container-b", cookies={"hsm_session": self.user_token})
        self.assertEqual(response.status_code, 403)
        response = self.client.simulate_get("/api/containers/container-b", cookies={"hsm_session": self.admin_token})
        self.assertEqual(response.status_code, 200)
