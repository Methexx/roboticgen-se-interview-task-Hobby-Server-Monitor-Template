"""Configured-allocation quotas; telemetry is never used for authorization."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3


@dataclass(frozen=True)
class Allocation:
    ram_bytes: int
    cpu_cores: int
    disk_bytes: int


@dataclass(frozen=True)
class Quota(Allocation):
    pass


@dataclass(frozen=True)
class Usage(Allocation):
    pass


class QuotaError(ValueError):
    def __init__(self, dimension: str, allocated: int, requested_delta: int, quota: int) -> None:
        super().__init__(f"{dimension} quota exceeded")
        self.dimension = dimension
        self.allocated = allocated
        self.requested_delta = requested_delta
        self.quota = quota


def _allocation(row: sqlite3.Row | tuple[object, ...]) -> Allocation:
    values = tuple(row)
    if any(value is None for value in values):
        raise ValueError("container allocation is unknown and cannot be charged safely")
    return Allocation(*(int(value) for value in values))


def usage_for_user(connection: sqlite3.Connection, user_id: int) -> Usage:
    """Sum each present container once when owned and/or assigned to a user."""
    row = connection.execute(
        """WITH charged_containers AS (
               SELECT id FROM containers WHERE lifecycle = 'present' AND owner_user_id = ?
               UNION
               SELECT c.id
               FROM containers AS c
               JOIN container_assignments AS a ON a.container_id = c.id
               WHERE c.lifecycle = 'present' AND a.user_id = ?
           )
           SELECT COALESCE(SUM(a.ram_bytes), 0), COALESCE(SUM(a.cpu_cores), 0),
                  COALESCE(SUM(a.disk_bytes), 0),
                  COALESCE(SUM(CASE WHEN a.container_id IS NULL OR a.ram_bytes IS NULL
                                     OR a.cpu_cores IS NULL OR a.disk_bytes IS NULL
                                    THEN 1 ELSE 0 END), 0)
           FROM charged_containers AS c
           LEFT JOIN container_allocations AS a ON a.container_id = c.id""",
        (user_id, user_id),
    ).fetchone()
    if row is None:
        return Usage(0, 0, 0)
    if row[3]:
        raise ValueError("container allocation is unknown and cannot be charged safely")
    return Usage(int(row[0]), int(row[1]), int(row[2]))


def quota_for_user(connection: sqlite3.Connection, user_id: int) -> Quota:
    row = connection.execute(
        "SELECT quota_ram_bytes, quota_cpu_cores, quota_disk_bytes FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        raise LookupError("user does not exist")
    return Quota(*_allocation(row).__dict__.values())


def require_within_quota(current: Usage, quota: Quota, delta: Allocation) -> None:
    for dimension, allocated, limit, requested in (
        ("ram_bytes", current.ram_bytes, quota.ram_bytes, delta.ram_bytes),
        ("cpu_cores", current.cpu_cores, quota.cpu_cores, delta.cpu_cores),
        ("disk_bytes", current.disk_bytes, quota.disk_bytes, delta.disk_bytes),
    ):
        if allocated + requested > limit:
            raise QuotaError(dimension, allocated, requested, limit)


def require_allocation_change(
    connection: sqlite3.Connection, container_id: str, replacement: Allocation
) -> None:
    """Validate a new configured allocation against every current chargee."""
    current_row = connection.execute(
        "SELECT ram_bytes, cpu_cores, disk_bytes FROM container_allocations WHERE container_id = ?",
        (container_id,),
    ).fetchone()
    if current_row is None:
        raise LookupError("container allocation is unknown")
    current = _allocation(current_row)
    user_rows = connection.execute(
        """SELECT DISTINCT user_id FROM (
               SELECT owner_user_id AS user_id FROM containers WHERE id = ? AND lifecycle = 'present'
               UNION ALL
               SELECT user_id FROM container_assignments WHERE container_id = ?
           ) WHERE user_id IS NOT NULL""",
        (container_id, container_id),
    ).fetchall()
    delta = Allocation(
        replacement.ram_bytes - current.ram_bytes,
        replacement.cpu_cores - current.cpu_cores,
        replacement.disk_bytes - current.disk_bytes,
    )
    for row in user_rows:
        user_id = int(row[0])
        usage = usage_for_user(connection, user_id)
        quota = quota_for_user(connection, user_id)
        require_within_quota(usage, quota, delta)
