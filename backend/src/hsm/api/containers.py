"""SQLite snapshot reads; browser requests never poll LXD for metrics."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import hashlib
import re
import uuid
import time

import falcon

from hsm.db import connect
from hsm.operations import OperationConflict, OperationService, Reservation
from hsm.lxd.discovery import DiscoveryError
from hsm.lxd.capacity import CapacityService
from hsm.lxd.creation import LxdCreator


def _item(row: sqlite3.Row | tuple[object, ...]) -> dict[str, object]:
    values = tuple(row)
    return {
        "id": values[0], "project": values[1], "name": values[2],
        "state": values[3], "image": values[4], "os_version": values[5],
        "ipv4": json.loads(values[6]) if values[6] else [], "uptime_s": values[7],
        "processes": values[8], "metrics": {
            "cpu_pct": values[9], "ram_used_bytes": values[10], "disk_used_bytes": values[11],
            "net_rx_bytes": values[12], "net_tx_bytes": values[13],
            "net_rx_bps": values[14], "net_tx_bps": values[15],
        }, "sampled_at": values[16], "error_code": values[17],
    }


_SELECT = """SELECT c.id, c.project, c.current_name, m.state, m.image, m.os_version,
                    m.ipv4_json, m.uptime_s, m.processes, m.cpu_pct, m.ram_used_bytes,
                    m.disk_used_bytes, m.net_rx_bytes, m.net_tx_bytes, m.net_rx_bps,
                    m.net_tx_bps, m.sampled_at, m.error_code
             FROM containers AS c LEFT JOIN metrics_latest AS m ON m.container_id = c.id"""


class ContainersResource:
    def __init__(self, database_path: Path, capacity: CapacityService | None = None, creator: LxdCreator | None = None) -> None:
        self._database_path = database_path
        self._create = CreateContainerResource(database_path, capacity, creator) if capacity and creator else None

    def on_get(self, request: falcon.Request, response: falcon.Response) -> None:
        connection = connect(self._database_path)
        try:
            if request.context.user.role == "admin":
                rows = connection.execute(_SELECT + " WHERE c.lifecycle = 'present' ORDER BY c.project, c.current_name").fetchall()
            else:
                rows = connection.execute(
                    _SELECT + " JOIN container_assignments AS a ON a.container_id = c.id "
                    "WHERE c.lifecycle = 'present' AND a.user_id = ? ORDER BY c.project, c.current_name",
                    (request.context.user.id,),
                ).fetchall()
        finally:
            connection.close()
        response.media = {"items": [_item(row) for row in rows], "next_cursor": None}

    def on_post(self, request: falcon.Request, response: falcon.Response) -> None:
        if self._create is None:
            raise falcon.HTTPServiceUnavailable(description="Container creation is unavailable")
        self._create.on_post(request, response)


class CreateContainerResource:
    _fields = {"name", "image", "pool", "network", "profile", "ram_bytes", "cpu_cores", "cpu_allowance_pct", "disk_bytes", "process_limit", "autostart", "ephemeral", "start", "description"}

    def __init__(self, database_path: Path, capacity: CapacityService, creator: LxdCreator) -> None:
        self._path, self._capacity, self._creator = database_path, capacity, creator

    def on_post(self, request: falcon.Request, response: falcon.Response) -> None:
        if request.content_type != "application/json": raise falcon.HTTPUnsupportedMediaType(description="Content-Type must be application/json")
        if request.content_length is not None and request.content_length > 16384: raise falcon.HTTPPayloadTooLarge(description="Request body is too large")
        payload = request.media
        if not isinstance(payload, dict) or set(payload) != self._fields: raise falcon.HTTPBadRequest(description="Request body has missing or unknown fields")
        key = request.get_header("Idempotency-Key")
        if not key or len(key) > 128: raise falcon.HTTPBadRequest(description="Idempotency-Key is required")
        self._validate(payload)
        capacity = self._capacity.get(request.context.user.id)
        if not capacity["feasible"]: raise falcon.HTTPConflict(description="Creation is not feasible: " + ", ".join(capacity["constraints"]))
        self._validate_options(payload, capacity)
        request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()
        connection = connect(self._path)
        try:
            duplicate = connection.execute("SELECT 1 FROM containers WHERE project=? AND current_name=? AND lifecycle='present'", (capacity["creation_project"], payload["name"])).fetchone()
        finally:
            connection.close()
        if duplicate: raise falcon.HTTPConflict(description="A container with this name already exists")
        ram, cpu, disk = payload["ram_bytes"], payload["cpu_cores"], payload["disk_bytes"]
        operation = OperationService(self._path)
        try:
            operation_id, created = operation.begin(actor_id=request.context.user.id, kind="container.create", request_hash=request_hash, idempotency_key=key, target_id=None, reservations=(Reservation("user", str(request.context.user.id), ram, cpu, disk), Reservation("host", "global", ram, cpu, disk), Reservation("pool", payload["pool"], 0, 0, disk)))
        except (OperationConflict, ValueError) as error:
            raise falcon.HTTPConflict(description=str(error)) from error
        if not created:
            response.status = falcon.HTTP_202; response.media = {"operation_id": operation_id}; return
        config = {"limits.memory": str(ram), "limits.cpu": str(cpu), "limits.cpu.allowance": f"{payload['cpu_allowance_pct']}%", "limits.processes": str(payload["process_limit"]), "security.privileged": "false", "security.nesting": "false", "boot.autostart": str(payload["autostart"]).lower(), "volatile.apply_template": "create"}
        lxd_payload = {"name": payload["name"], "type": "container", "source": {"type": "image", "fingerprint": next(item["fingerprint"] for item in capacity["images"] if item["alias"] == payload["image"])}, "profiles": [payload["profile"]], "config": config, "devices": {"root": {"type": "disk", "path": "/", "pool": payload["pool"], "size": str(disk)}, "eth0": {"type": "nic", "network": payload["network"]}}, "ephemeral": payload["ephemeral"], "description": payload["description"]}
        started = time.monotonic()
        try:
            result = self._creator.create(capacity["creation_project"], lxd_payload, payload["start"])
        except DiscoveryError as error:
            operation.finish(operation_id, status="unknown", error_code=error.kind)
            import logging
            logging.getLogger("hsm.lxd").warning("lxd_create_unconfirmed operation_id=%s exception=%s category=%s elapsed_ms=%d", operation_id, type(error.__cause__).__name__ if error.__cause__ else type(error).__name__, error.kind, int((time.monotonic()-started)*1000))
            raise falcon.HTTPServiceUnavailable(description=error.message) from error
        container_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"lxd:{capacity['creation_project']}:{result['lxd_uuid']}"))
        try:
            connection = connect(self._path)
            with connection:
                connection.execute("INSERT INTO containers(id,project,lxd_uuid,current_name,owner_user_id,managed,isolation_status,lifecycle,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,1,'verified','present',datetime('now'),datetime('now'))", (container_id, capacity["creation_project"], result["lxd_uuid"], result["name"], request.context.user.id))
                connection.execute("INSERT INTO container_allocations(container_id,ram_bytes,cpu_cores,cpu_allowance_pct,disk_bytes,pool,config_fingerprint,verified_at) VALUES(?,?,?,?,?,?,?,datetime('now'))", (container_id, ram, cpu, payload["cpu_allowance_pct"], disk, payload["pool"], request_hash))
                connection.execute("UPDATE operations SET target_id=?,status='succeeded',completed_at=datetime('now') WHERE id=?", (container_id, operation_id))
                connection.execute("DELETE FROM allocation_reservations WHERE operation_id=?", (operation_id,))
                connection.execute("INSERT INTO audit_log(ts,actor_user_id,action,target_type,target_id,target_name,operation_id,detail_json,outcome) VALUES(datetime('now'),?,?,?,?,?,?,?, 'ok')", (request.context.user.id, "container.create", "container", container_id, result["name"], operation_id, "{}"))
            connection.close()
        except Exception:
            operation.finish(operation_id, status="unknown", error_code="persistence_failed")
            raise
        response.status = falcon.HTTP_201
        response.media = {"id": container_id, "name": result["name"], "operation_id": operation_id}

    @staticmethod
    def _validate(payload: dict[str, object]) -> None:
        if not isinstance(payload["name"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{2,62}", payload["name"]) or payload["name"].endswith("-"): raise falcon.HTTPBadRequest(description="name is invalid")
        for field in ("image", "pool", "network", "profile", "description"):
            if not isinstance(payload[field], str) or len(payload[field]) > 256: raise falcon.HTTPBadRequest(description=f"{field} is invalid")
        for field in ("ram_bytes", "cpu_cores", "cpu_allowance_pct", "disk_bytes", "process_limit"):
            if type(payload[field]) is not int or payload[field] <= 0: raise falcon.HTTPBadRequest(description=f"{field} is invalid")
        if payload["cpu_allowance_pct"] > 100 or payload["process_limit"] > 65535 or any(type(payload[field]) is not bool for field in ("autostart", "ephemeral", "start")): raise falcon.HTTPBadRequest(description="container settings are invalid")

    @staticmethod
    def _validate_options(payload: dict[str, object], capacity: dict[str, object]) -> None:
        if payload["image"] not in {item["alias"] for item in capacity["images"]} or payload["pool"] not in {item["name"] for item in capacity["pools"]} or payload["network"] not in {item["name"] for item in capacity["networks"]} or payload["profile"] not in {item["name"] for item in capacity["profiles"]}: raise falcon.HTTPBadRequest(description="A selected LXD option is not allowed")
        bounds = capacity["bounds"]
        if any(payload[field] > bounds[field]["max"] for field in ("ram_bytes", "cpu_cores", "disk_bytes")): raise falcon.HTTPConflict(description="Requested allocation exceeds current capacity")
        selected = next(item for item in capacity["profiles"] if item["name"] == payload["profile"])
        if selected.get("root_pool") != payload["pool"] or selected.get("network") != payload["network"]:
            raise falcon.HTTPBadRequest(description="Profile does not match the selected root disk and network")
        pool = next(item for item in capacity["pools"] if item["name"] == payload["pool"])
        if payload["disk_bytes"] > pool.get("available_bytes", 0):
            raise falcon.HTTPConflict(description="Requested disk allocation exceeds selected pool capacity")


class ContainerResource:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def on_get(self, request: falcon.Request, response: falcon.Response, **params: str) -> None:
        connection = connect(self._database_path)
        try:
            row = connection.execute(
                _SELECT + " WHERE c.id = ? AND c.lifecycle = 'present'",
                (request.context.route_params["container_id"],),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise falcon.HTTPNotFound(description="Container not found")
        response.media = _item(row)
