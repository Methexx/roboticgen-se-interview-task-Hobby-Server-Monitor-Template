"""Central authorization for stable application container IDs."""

from __future__ import annotations

import sqlite3

import falcon


def require_container_access(connection: sqlite3.Connection, user_id: int, container_id: str) -> None:
    """Allow active admins or explicit active-user assignments only.

    The container is looked up by immutable application ID before any future LXD
    project/name resolution. Existing but unassigned IDs deliberately return 403.
    """
    container = connection.execute(
        "SELECT id FROM containers WHERE id = ? AND lifecycle = 'present'", (container_id,)
    ).fetchone()
    if container is None:
        raise falcon.HTTPNotFound(description="Container not found")
    user = connection.execute(
        "SELECT role, status FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if user is None or user[1] != "active":
        raise falcon.HTTPForbidden(description="Container access is not allowed")
    if user[0] == "admin":
        return
    assigned = connection.execute(
        "SELECT 1 FROM container_assignments WHERE user_id = ? AND container_id = ?",
        (user_id, container_id),
    ).fetchone()
    if assigned is None:
        raise falcon.HTTPForbidden(description="Container access is not allowed")
