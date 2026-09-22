"""SQLite snapshot reads; browser requests never poll LXD for metrics."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import hashlib
import re
import uuid
import time
from datetime import datetime, timezone

import falcon

from hsm.db import connect
from hsm.operations import OperationConflict, OperationService, Reservation
from hsm.lxd.discovery import DiscoveryError
from hsm.lxd.capacity import CapacityService
from hsm.lxd.creation import LxdCreator
from hsm.quota import Allocation
from hsm.quota.service import require_allocation_change


def _item(row: sqlite3.Row | tuple[object, ...]) -> dict[str, object]:
    values = tuple(row)
    stale = values[16] is None or (datetime.now(timezone.utc) - datetime.fromisoformat(values[16])).total_seconds() > 30
    return {
        "id": values[0], "project": values[1], "name": values[2],
        "state": values[3], "image": values[4], "os_version": values[5],
        "ipv4": json.loads(values[6]) if values[6] else [], "uptime_s": values[7],
        "processes": values[8], "metrics": {
            "cpu_pct": values[9], "ram_used_bytes": values[10], "disk_used_bytes": values[11],
            "net_rx_bytes": values[12], "net_tx_bytes": values[13],
            "net_rx_bps": values[14], "net_tx_bps": values[15],
        }, "sampled_at": values[16], "error_code": values[17], "stale": stale,
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
            statuses = {key: json.loads(value) for key, value in connection.execute("SELECT key,value_json FROM collector_status")}
        finally:
            connection.close()
        response.media = {"items": [_item(row) for row in rows], "next_cursor": None, "collector": statuses}

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
    def __init__(self, database_path: Path, creator: LxdCreator | None = None) -> None:
        self._database_path = database_path
        self._mutation = ContainerMutationResource(database_path, creator) if creator else None

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

    def on_delete(self, request: falcon.Request, response: falcon.Response, **params: str) -> None:
        if self._mutation is None: raise falcon.HTTPServiceUnavailable(description="Container mutations are unavailable")
        self._mutation.on_delete(request, response)


class ContainerMutationResource:
    def __init__(self, database_path: Path, creator: LxdCreator) -> None:
        self._path, self._creator = database_path, creator

    def on_post(self, request: falcon.Request, response: falcon.Response, **params: str) -> None:
        action = request.context.route_params["action"]
        if action not in {"start", "stop", "restart", "freeze", "unfreeze"}:
            raise falcon.HTTPNotFound(description="Unsupported container action")
        self._mutate(request, response, action)

    def on_delete(self, request: falcon.Request, response: falcon.Response, **params: str) -> None:
        if request.content_type != "application/json" or not isinstance(request.media, dict) or set(request.media) != {"confirm_name"}:
            raise falcon.HTTPBadRequest(description="Deletion requires confirm_name")
        connection = connect(self._path)
        try:
            row = connection.execute("SELECT current_name FROM containers WHERE id=? AND lifecycle='present'", (request.context.route_params["container_id"],)).fetchone()
        finally: connection.close()
        if row is None or request.media["confirm_name"] != row[0]:
            raise falcon.HTTPBadRequest(description="Deletion confirmation does not match the container name")
        self._mutate(request, response, "delete")

    def _mutate(self, request: falcon.Request, response: falcon.Response, action: str) -> None:
        key = request.get_header("Idempotency-Key")
        if not key or len(key) > 128: raise falcon.HTTPBadRequest(description="Idempotency-Key is required")
        identifier = request.context.route_params["container_id"]
        connection = connect(self._path)
        try:
            row = connection.execute("SELECT project,current_name,managed FROM containers WHERE id=? AND lifecycle='present'", (identifier,)).fetchone()
        finally: connection.close()
        if row is None or not row[2]: raise falcon.HTTPNotFound(description="Managed container not found")
        service = OperationService(self._path)
        try:
            operation_id, created = service.begin(actor_id=request.context.user.id, kind=f"container.{action}", request_hash=hashlib.sha256(f"{identifier}:{action}".encode()).hexdigest(), idempotency_key=key, target_id=identifier)
        except OperationConflict as error: raise falcon.HTTPConflict(description=str(error)) from error
        if not created:
            response.status=falcon.HTTP_202; response.media={"operation_id":operation_id}; return
        try:
            self._creator.action(row[0], row[1], action)
        except DiscoveryError as error:
            service.finish(operation_id,status="unknown",error_code=error.kind)
            raise falcon.HTTPServiceUnavailable(description=error.message) from error
        connection=connect(self._path)
        try:
            with connection:
                if action == "delete":
                    connection.execute("DELETE FROM container_assignments WHERE container_id=?", (identifier,))
                    connection.execute("UPDATE containers SET lifecycle='deleted',deleted_at=datetime('now') WHERE id=?", (identifier,))
                connection.execute("UPDATE operations SET status='succeeded',completed_at=datetime('now') WHERE id=?", (operation_id,))
                connection.execute("INSERT INTO audit_log(ts,actor_user_id,action,target_type,target_id,target_name,operation_id,detail_json,outcome) VALUES(datetime('now'),?,?,?,?,?,?,?, 'ok')", (request.context.user.id, f"container.{action}", "container", identifier, row[1], operation_id, "{}"))
        finally: connection.close()
        response.status=falcon.HTTP_204


class ContainerActionResource:
    def __init__(self, database_path: Path, creator: LxdCreator) -> None:
        self._mutation = ContainerMutationResource(database_path, creator)

    def on_post(self, request: falcon.Request, response: falcon.Response, **params: str) -> None:
        self._mutation.on_post(request, response)


class ContainerLimitsResource:
    _fields = {"ram_bytes", "cpu_cores", "cpu_allowance_pct", "disk_bytes", "process_limit"}
    def __init__(self, database_path: Path, creator: LxdCreator) -> None:
        self._path, self._creator = database_path, creator
    def on_patch(self, request: falcon.Request, response: falcon.Response, **params: str) -> None:
        if request.content_type != "application/json" or not isinstance(request.media, dict) or not request.media or set(request.media) - self._fields:
            raise falcon.HTTPBadRequest(description="Limit update has missing or unknown fields")
        values = request.media
        if any(type(value) is not int or value <= 0 for value in values.values()) or values.get("cpu_allowance_pct", 1) > 100 or values.get("process_limit", 1) > 65535:
            raise falcon.HTTPBadRequest(description="Limit values are invalid")
        key=request.get_header("Idempotency-Key")
        if not key or len(key)>128: raise falcon.HTTPBadRequest(description="Idempotency-Key is required")
        identifier=request.context.route_params["container_id"]
        connection=connect(self._path)
        try:
            row=connection.execute("SELECT c.project,c.current_name,c.managed,a.ram_bytes,a.cpu_cores,a.cpu_allowance_pct,a.disk_bytes,a.pool FROM containers c JOIN container_allocations a ON a.container_id=c.id WHERE c.id=? AND c.lifecycle='present'",(identifier,)).fetchone()
            if row is None or not row[2]: raise falcon.HTTPNotFound(description="Managed container not found")
            if any(row[index] is None for index in (3,4,6,7)): raise falcon.HTTPConflict(description="Container allocation is unknown")
            replacement=Allocation(values.get("ram_bytes",row[3]),values.get("cpu_cores",row[4]),values.get("disk_bytes",row[6]))
            if replacement.disk_bytes < row[6]: raise falcon.HTTPBadRequest(description="Disk shrink is not supported")
            require_allocation_change(connection,identifier,replacement)
        finally: connection.close()
        delta=Allocation(max(0,replacement.ram_bytes-row[3]),max(0,replacement.cpu_cores-row[4]),max(0,replacement.disk_bytes-row[6]))
        request_hash=hashlib.sha256(json.dumps(values,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        service=OperationService(self._path)
        try:
            operation_id,created=service.begin(actor_id=request.context.user.id,kind="container.limits",request_hash=request_hash,idempotency_key=key,target_id=identifier,reservations=(Reservation("host","global",delta.ram_bytes,delta.cpu_cores,delta.disk_bytes),Reservation("pool",row[7],0,0,delta.disk_bytes)))
        except OperationConflict as error: raise falcon.HTTPConflict(description=str(error)) from error
        if not created: response.status=falcon.HTTP_202;response.media={"operation_id":operation_id};return
        config={}
        if "ram_bytes" in values: config["limits.memory"]=str(values["ram_bytes"])
        if "cpu_cores" in values: config["limits.cpu"]=str(values["cpu_cores"])
        if "cpu_allowance_pct" in values: config["limits.cpu.allowance"]=f"{values['cpu_allowance_pct']}%"
        if "process_limit" in values: config["limits.processes"]=str(values["process_limit"])
        try: self._creator.update_limits(row[0],row[1],config,values.get("disk_bytes"))
        except DiscoveryError as error:
            service.finish(operation_id,status="unknown",error_code=error.kind);raise falcon.HTTPServiceUnavailable(description=error.message) from error
        connection=connect(self._path)
        try:
            with connection:
                connection.execute("UPDATE container_allocations SET ram_bytes=?,cpu_cores=?,cpu_allowance_pct=?,disk_bytes=?,verified_at=datetime('now') WHERE container_id=?",(replacement.ram_bytes,replacement.cpu_cores,values.get("cpu_allowance_pct",row[5]),replacement.disk_bytes,identifier))
                connection.execute("UPDATE operations SET status='succeeded',completed_at=datetime('now') WHERE id=?",(operation_id,));connection.execute("DELETE FROM allocation_reservations WHERE operation_id=?",(operation_id,))
                connection.execute("INSERT INTO audit_log(ts,actor_user_id,action,target_type,target_id,target_name,operation_id,detail_json,outcome) VALUES(datetime('now'),?,?,?,?,?,?,?, 'ok')",(request.context.user.id,"container.limits","container",identifier,row[1],operation_id,json.dumps(values,sort_keys=True)))
        finally: connection.close()
        response.status=falcon.HTTP_204
