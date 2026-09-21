"""Transactional user, role, quota, and assignment administration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from hsm.db import connect
from hsm.quota.service import Allocation, Quota, quota_for_user, require_within_quota, usage_for_user


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class User:
    id: int
    email: str
    role: str
    status: str
    quota: Quota


class UserConflict(ValueError):
    pass


def _email(value: str) -> str:
    normalized = value.strip().lower()
    if normalized.count("@") != 1 or normalized.startswith("@") or normalized.endswith("@"):
        raise ValueError("email must be a valid address")
    if len(normalized) > 254:
        raise ValueError("email is too long")
    return normalized


def _quota(value: Allocation) -> Quota:
    if min(value.ram_bytes, value.cpu_cores, value.disk_bytes) < 0:
        raise ValueError("quota values must not be negative")
    return Quota(value.ram_bytes, value.cpu_cores, value.disk_bytes)


class UserService:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def invite(self, *, email: str, role: str, quota: Allocation, invited_by: int, now: str | None = None) -> User:
        if role not in {"admin", "user"}:
            raise ValueError("role must be admin or user")
        normalized_email, checked_quota = _email(email), _quota(quota)
        connection = connect(self._database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT id FROM users WHERE email = ?", (normalized_email,)).fetchone()
            if existing is not None:
                raise UserConflict("email is already invited")
            cursor = connection.execute(
                """INSERT INTO users (
                    email, role, status, quota_ram_bytes, quota_cpu_cores, quota_disk_bytes,
                    invited_by, created_at
                ) VALUES (?, ?, 'invited', ?, ?, ?, ?, ?)""",
                (normalized_email, role, checked_quota.ram_bytes, checked_quota.cpu_cores,
                 checked_quota.disk_bytes, invited_by, now or _now()),
            )
            connection.commit()
            return User(cursor.lastrowid, normalized_email, role, "invited", checked_quota)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list_users(self) -> list[User]:
        connection = connect(self._database_path)
        try:
            rows = connection.execute(
                "SELECT id, email, role, status, quota_ram_bytes, quota_cpu_cores, quota_disk_bytes FROM users ORDER BY email"
            ).fetchall()
            return [User(row[0], row[1], row[2], row[3], Quota(row[4], row[5], row[6])) for row in rows]
        finally:
            connection.close()

    def update(self, *, user_id: int, role: str | None = None, quota: Allocation | None = None) -> None:
        if role is not None and role not in {"admin", "user"}:
            raise ValueError("role must be admin or user")
        connection = connect(self._database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            target = connection.execute("SELECT role, status FROM users WHERE id = ?", (user_id,)).fetchone()
            if target is None:
                raise LookupError("user does not exist")
            if target[0] == "admin" and target[1] == "active" and role == "user":
                active_admins = connection.execute(
                    "SELECT COUNT(*) FROM users WHERE role = 'admin' AND status = 'active'"
                ).fetchone()[0]
                if active_admins <= 1:
                    raise UserConflict("cannot demote the last active admin")
            if quota is not None:
                checked_quota = _quota(quota)
                require_within_quota(usage_for_user(connection, user_id), checked_quota, Allocation(0, 0, 0))
                connection.execute(
                    "UPDATE users SET quota_ram_bytes = ?, quota_cpu_cores = ?, quota_disk_bytes = ? WHERE id = ?",
                    (checked_quota.ram_bytes, checked_quota.cpu_cores, checked_quota.disk_bytes, user_id),
                )
            if role is not None:
                connection.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def revoke(self, *, user_id: int) -> None:
        connection = connect(self._database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            target = connection.execute("SELECT role, status FROM users WHERE id = ?", (user_id,)).fetchone()
            if target is None:
                raise LookupError("user does not exist")
            if target[0] == "admin" and target[1] == "active":
                active_admins = connection.execute(
                    "SELECT COUNT(*) FROM users WHERE role = 'admin' AND status = 'active'"
                ).fetchone()[0]
                if active_admins <= 1:
                    raise UserConflict("cannot revoke the last active admin")
            connection.execute("UPDATE users SET status = 'revoked' WHERE id = ?", (user_id,))
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            connection.execute("DELETE FROM container_assignments WHERE user_id = ?", (user_id,))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def assign(self, *, user_id: int, container_id: str, assigned_by: int, now: str | None = None) -> None:
        connection = connect(self._database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            user = connection.execute("SELECT status FROM users WHERE id = ?", (user_id,)).fetchone()
            container = connection.execute(
                "SELECT owner_user_id, isolation_status FROM containers WHERE id = ? AND lifecycle = 'present'",
                (container_id,),
            ).fetchone()
            if user is None or user[0] not in {"invited", "active"}:
                raise UserConflict("user cannot receive assignments")
            if container is None:
                raise LookupError("container does not exist")
            if container[1] != "safe":
                raise UserConflict("container isolation is not approved")
            present = connection.execute(
                "SELECT 1 FROM container_assignments WHERE user_id = ? AND container_id = ?",
                (user_id, container_id),
            ).fetchone()
            if present is None and container[0] != user_id:
                allocation = connection.execute(
                    "SELECT ram_bytes, cpu_cores, disk_bytes FROM container_allocations WHERE container_id = ?",
                    (container_id,),
                ).fetchone()
                if allocation is None:
                    raise UserConflict("container allocation is unknown")
                values = tuple(allocation)
                if any(value is None for value in values):
                    raise UserConflict("container allocation is unknown")
                require_within_quota(
                    usage_for_user(connection, user_id), quota_for_user(connection, user_id), Allocation(*values)
                )
            connection.execute(
                """INSERT INTO container_assignments(user_id, container_id, assigned_by, assigned_at)
                   VALUES (?, ?, ?, ?) ON CONFLICT(user_id, container_id) DO NOTHING""",
                (user_id, container_id, assigned_by, now or _now()),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def unassign(self, *, user_id: int, container_id: str) -> None:
        connection = connect(self._database_path)
        try:
            with connection:
                connection.execute(
                    "DELETE FROM container_assignments WHERE user_id = ? AND container_id = ?",
                    (user_id, container_id),
                )
        finally:
            connection.close()

    def transfer_owner(self, *, container_id: str, user_id: int) -> None:
        """Move an ownership charge only after serialised quota validation."""
        connection = connect(self._database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            user = connection.execute("SELECT status FROM users WHERE id = ?", (user_id,)).fetchone()
            allocation = connection.execute(
                """SELECT a.ram_bytes, a.cpu_cores, a.disk_bytes
                   FROM containers AS c JOIN container_allocations AS a ON a.container_id = c.id
                   WHERE c.id = ? AND c.lifecycle = 'present'""",
                (container_id,),
            ).fetchone()
            if user is None or user[0] not in {"invited", "active"}:
                raise UserConflict("new owner must be invited or active")
            if allocation is None or any(value is None for value in allocation):
                raise UserConflict("container allocation is unknown")
            already_charged = connection.execute(
                "SELECT 1 FROM containers WHERE id = ? AND owner_user_id = ? UNION SELECT 1 FROM container_assignments WHERE container_id = ? AND user_id = ?",
                (container_id, user_id, container_id, user_id),
            ).fetchone()
            if already_charged is None:
                require_within_quota(
                    usage_for_user(connection, user_id), quota_for_user(connection, user_id), Allocation(*allocation)
                )
            connection.execute("UPDATE containers SET owner_user_id = ? WHERE id = ?", (user_id, container_id))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
