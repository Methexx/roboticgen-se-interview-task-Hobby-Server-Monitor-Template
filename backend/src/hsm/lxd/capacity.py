"""Safe capacity calculation and successful inventory reconciliation."""
from __future__ import annotations

from pathlib import Path
import uuid

from hsm.db import connect
from hsm.lxd.discovery import LxdDiscovery
from hsm.quota import quota_for_user, usage_for_user


class CapacityService:
    def __init__(self, path: Path, discovery: LxdDiscovery, creation_project: str = "hsm") -> None:
        self.path, self.discovery, self.creation_project = path, discovery, creation_project

    def get(self, user_id: int) -> dict[str, object]:
        inventory = self.discovery.inventory()
        capacity = self.discovery.capacity(self.creation_project)
        if not inventory["partial"]:
            self._reconcile(inventory["instances"])
        connection = connect(self.path)
        try:
            quota, usage = quota_for_user(connection, user_id), usage_for_user(connection, user_id)
            user_reserved = connection.execute("SELECT COALESCE(SUM(delta_ram_bytes),0), COALESCE(SUM(delta_cpu_cores),0), COALESCE(SUM(delta_disk_bytes),0) FROM allocation_reservations WHERE user_id=? AND scope='user'", (user_id,)).fetchone()
            host_reserved = connection.execute("SELECT COALESCE(SUM(delta_ram_bytes),0), COALESCE(SUM(delta_cpu_cores),0) FROM allocation_reservations WHERE scope='host'").fetchone()
            known_ram, known_cpu, unknown = connection.execute("SELECT COALESCE(SUM(a.ram_bytes),0), COALESCE(SUM(a.cpu_cores),0), COALESCE(SUM(CASE WHEN a.container_id IS NULL OR a.ram_bytes IS NULL OR a.cpu_cores IS NULL OR a.disk_bytes IS NULL THEN 1 ELSE 0 END),0) FROM containers c LEFT JOIN container_allocations a ON a.container_id=c.id WHERE c.lifecycle='present'").fetchone()
            pool_reserved = {row[0]: row[1] for row in connection.execute("SELECT scope_key, COALESCE(SUM(delta_disk_bytes),0) FROM allocation_reservations WHERE scope='pool' GROUP BY scope_key")}
            pool_allocated = {row[0]: row[1] for row in connection.execute("SELECT pool, COALESCE(SUM(disk_bytes),0) FROM container_allocations GROUP BY pool")}
        finally:
            connection.close()
        host = capacity["host"]
        cpu, ram = host.get("cpu_total"), host.get("memory_total")
        pools = [pool for pool in capacity["pools"] if pool.get("quota_supported")]
        for pool in pools:
            pool["available_bytes"] = max(0, int(pool["free_bytes"] * 0.9) - pool_reserved.get(pool["name"], 0) - pool_allocated.get(pool["name"], 0))
        networks = [network for network in capacity["networks"] if network.get("managed") and network.get("type") == "bridge"]
        profiles = [profile for profile in capacity["profiles"] if profile.get("safe")]
        constraints: list[str] = []
        if inventory["partial"]: constraints.append("inventory_partial")
        if not isinstance(cpu, int) or cpu <= 0 or not isinstance(ram, int) or ram <= 0: constraints.append("host_capacity_unknown")
        if not pools: constraints.append("no_quota_capable_pool")
        if not networks: constraints.append("no_managed_bridge")
        if not capacity["images"]: constraints.append("no_creation_image")
        if not profiles: constraints.append("no_safe_profile")
        remaining = {"ram_bytes": quota.ram_bytes - usage.ram_bytes - user_reserved[0], "cpu_cores": quota.cpu_cores - usage.cpu_cores - user_reserved[1], "disk_bytes": quota.disk_bytes - usage.disk_bytes - user_reserved[2]}
        if unknown: constraints.append("existing_allocation_unknown")
        host_remaining_cpu = (int(cpu * 0.9) - known_cpu - host_reserved[1]) if isinstance(cpu, int) else 0
        host_remaining_ram = (int(ram * 0.9) - known_ram - host_reserved[0]) if isinstance(ram, int) else 0
        pool_free = max((pool["available_bytes"] for pool in pools), default=0)
        if pool_free <= 0: constraints.append("pool_capacity_unknown_or_exhausted")
        bounds = {
            "ram_bytes": {"max": max(0, min(remaining["ram_bytes"], host_remaining_ram))},
            "cpu_cores": {"max": max(0, min(remaining["cpu_cores"], host_remaining_cpu))},
            "disk_bytes": {"max": max(0, min(remaining["disk_bytes"], pool_free))},
        }
        if any(value["max"] <= 0 for value in bounds.values()): constraints.append("quota_or_capacity_exhausted")
        return {"feasible": not constraints, "constraints": constraints, "creation_project": self.creation_project,
                "host": {"cpu_total": cpu, "memory_total": ram, "cpu_reserve": int(cpu * 0.1) if isinstance(cpu, int) else None, "memory_reserve_bytes": int(ram * 0.1) if isinstance(ram, int) else None},
                "pools": pools, "networks": networks, "images": capacity["images"], "profiles": profiles,
                "bounds": bounds, "partial": inventory["partial"]}

    def _reconcile(self, instances: list[dict[str, str]]) -> None:
        connection = connect(self.path)
        try:
            with connection:
                for item in instances:
                    identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, f"lxd:{item['project']}:{item['lxd_uuid']}"))
                    connection.execute("""INSERT INTO containers(id,project,lxd_uuid,current_name,managed,isolation_status,lifecycle,first_seen_at,last_seen_at)
                    VALUES(?,?,?,?,0,'unknown','present',datetime('now'),datetime('now'))
                    ON CONFLICT(project,lxd_uuid) DO UPDATE SET current_name=excluded.current_name,last_seen_at=excluded.last_seen_at,lifecycle='present'""", (identifier, item["project"], item["lxd_uuid"], item["name"]))
        finally:
            connection.close()
