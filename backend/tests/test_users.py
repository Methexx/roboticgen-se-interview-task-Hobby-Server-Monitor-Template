from pathlib import Path
import sqlite3
import tempfile
import unittest

import falcon

from hsm.authz.access import require_container_access
from hsm.db import connect, migrate
from hsm.quota import Allocation, QuotaError, require_allocation_change, usage_for_user
from hsm.users import UserConflict, UserService


NOW = "2026-09-22T00:00:00+00:00"


class UserServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "hsm.sqlite"
        migrate(self.database)
        self.service = UserService(self.database)
        self.admin_id = self._user("admin@example.test", "admin", "active", 16, 16, 16)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _user(self, email: str, role: str, status: str, ram: int, cpu: int, disk: int) -> int:
        connection = connect(self.database)
        try:
            with connection:
                cursor = connection.execute(
                    """INSERT INTO users (
                        email, role, status, quota_ram_bytes, quota_cpu_cores, quota_disk_bytes, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (email, role, status, ram, cpu, disk, NOW),
                )
                return cursor.lastrowid
        finally:
            connection.close()

    def _container(self, container_id: str, owner_id: int | None, allocation: Allocation) -> None:
        connection = connect(self.database)
        try:
            with connection:
                connection.execute(
                    """INSERT INTO containers (
                        id, project, current_name, owner_user_id, managed, isolation_status, lifecycle,
                        first_seen_at, last_seen_at
                    ) VALUES (?, 'hsm', ?, ?, 1, 'safe', 'present', ?, ?)""",
                    (container_id, container_id, owner_id, NOW, NOW),
                )
                connection.execute(
                    """INSERT INTO container_allocations (
                        container_id, ram_bytes, cpu_cores, disk_bytes, verified_at
                    ) VALUES (?, ?, ?, ?, ?)""",
                    (container_id, allocation.ram_bytes, allocation.cpu_cores, allocation.disk_bytes, NOW),
                )
        finally:
            connection.close()

    def test_invite_normalizes_email_and_lists_user(self) -> None:
        invited = self.service.invite(
            email="  User@Example.Test ", role="user", quota=Allocation(2, 1, 4),
            invited_by=self.admin_id, now=NOW,
        )
        self.assertEqual(invited.email, "user@example.test")
        self.assertEqual(invited.status, "invited")
        self.assertEqual([user.email for user in self.service.list_users()], ["admin@example.test", "user@example.test"])

    def test_assignment_charges_a_shared_container_once_and_enforces_boundary(self) -> None:
        user_id = self._user("user@example.test", "user", "active", 2, 1, 4)
        self._container("container-a", self.admin_id, Allocation(2, 1, 4))
        self.service.assign(user_id=user_id, container_id="container-a", assigned_by=self.admin_id, now=NOW)
        self.service.assign(user_id=user_id, container_id="container-a", assigned_by=self.admin_id, now=NOW)
        connection = connect(self.database)
        try:
            self.assertEqual(usage_for_user(connection, user_id).ram_bytes, 2)
        finally:
            connection.close()
        self._container("container-b", self.admin_id, Allocation(1, 1, 1))
        with self.assertRaises(QuotaError) as error:
            self.service.assign(user_id=user_id, container_id="container-b", assigned_by=self.admin_id, now=NOW)
        self.assertEqual(error.exception.dimension, "ram_bytes")

    def test_quota_reduction_cannot_cut_below_current_charge(self) -> None:
        self._container("container-a", self.admin_id, Allocation(2, 1, 4))
        with self.assertRaises(QuotaError):
            self.service.update(user_id=self.admin_id, quota=Allocation(1, 16, 16))

    def test_owner_transfer_and_limit_increase_are_checked_against_user_quota(self) -> None:
        user_id = self._user("user@example.test", "user", "active", 2, 1, 4)
        self._container("container-a", self.admin_id, Allocation(2, 1, 4))
        self.service.transfer_owner(container_id="container-a", user_id=user_id)
        connection = connect(self.database)
        try:
            self.assertEqual(
                connection.execute("SELECT owner_user_id FROM containers WHERE id = 'container-a'").fetchone()[0],
                user_id,
            )
            with self.assertRaises(QuotaError):
                require_allocation_change(connection, "container-a", Allocation(3, 1, 4))
        finally:
            connection.close()

    def test_last_active_admin_cannot_be_demoted_or_revoked(self) -> None:
        with self.assertRaises(UserConflict):
            self.service.update(user_id=self.admin_id, role="user")
        with self.assertRaises(UserConflict):
            self.service.revoke(user_id=self.admin_id)

    def test_revocation_deletes_sessions_and_assignments(self) -> None:
        user_id = self._user("user@example.test", "user", "active", 4, 2, 8)
        self._container("container-a", self.admin_id, Allocation(1, 1, 1))
        self.service.assign(user_id=user_id, container_id="container-a", assigned_by=self.admin_id, now=NOW)
        connection = connect(self.database)
        try:
            with connection:
                connection.execute(
                    "INSERT INTO sessions(token_hash, user_id, created_at, last_seen_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                    ("hash", user_id, NOW, NOW, "2026-10-01T00:00:00+00:00"),
                )
        finally:
            connection.close()
        self.service.revoke(user_id=user_id)
        connection = connect(self.database)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user_id,)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM container_assignments WHERE user_id = ?", (user_id,)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT status FROM users WHERE id = ?", (user_id,)).fetchone()[0], "revoked")
        finally:
            connection.close()


class ContainerAccessTests(unittest.TestCase):
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
                    ) VALUES (?, ?, ?, 'active', 1, 1, 1, ?)""",
                    [(1, "admin@example.test", "admin", NOW), (2, "user@example.test", "user", NOW)],
                )
                connection.execute(
                    """INSERT INTO containers (
                        id, project, current_name, managed, isolation_status, lifecycle, first_seen_at, last_seen_at
                    ) VALUES ('container-a', 'hsm', 'container-a', 1, 'safe', 'present', ?, ?)""",
                    (NOW, NOW),
                )
        finally:
            connection.close()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_existing_unassigned_container_is_forbidden(self) -> None:
        connection = connect(self.database)
        try:
            with self.assertRaises(falcon.HTTPForbidden):
                require_container_access(connection, 2, "container-a")
            require_container_access(connection, 1, "container-a")
        finally:
            connection.close()

    def test_assignment_grants_access_by_stable_id(self) -> None:
        connection = connect(self.database)
        try:
            with connection:
                connection.execute(
                    "INSERT INTO container_assignments(user_id, container_id, assigned_by, assigned_at) VALUES (2, 'container-a', 1, ?)",
                    (NOW,),
                )
            require_container_access(connection, 2, "container-a")
        finally:
            connection.close()
