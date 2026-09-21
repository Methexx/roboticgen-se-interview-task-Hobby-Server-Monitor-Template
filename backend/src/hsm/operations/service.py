"""Durable intent/outcome and reservation records; no LXD call occurs here."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json, uuid
from hsm.db import connect

@dataclass(frozen=True)
class Reservation:
    scope: str; scope_key: str; ram_bytes: int; cpu_cores: int; disk_bytes: int
class OperationConflict(ValueError): pass
def _now(): return datetime.now(timezone.utc).isoformat()

class OperationService:
    def __init__(self, path: Path): self._path=path
    def begin(self, *, actor_id:int, kind:str, request_hash:str, idempotency_key:str, target_id:str|None, reservations:tuple[Reservation,...]=()) -> tuple[str, bool]:
        connection=connect(self._path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            prior=connection.execute("SELECT id, request_hash FROM operations WHERE actor_user_id=? AND idempotency_key=?",(actor_id,idempotency_key)).fetchone()
            if prior:
                if prior[1] != request_hash: raise OperationConflict("idempotency key was used with different input")
                connection.commit(); return prior[0], False
            operation_id=str(uuid.uuid4()); now=_now()
            connection.execute("INSERT INTO operations(id,actor_user_id,kind,target_id,request_hash,idempotency_key,status,created_at) VALUES(?,?,?,?,?,?, 'pending',?)",(operation_id,actor_id,kind,target_id,request_hash,idempotency_key,now))
            connection.execute("INSERT INTO audit_log(ts,actor_user_id,action,target_type,target_id,detail_json,outcome) VALUES(?,?,?,?,?,?, 'intent')",(now,actor_id,kind,"container",target_id,json.dumps({"request_hash":request_hash})))
            for item in reservations:
                connection.execute("INSERT INTO allocation_reservations(operation_id,user_id,scope,scope_key,delta_ram_bytes,delta_cpu_cores,delta_disk_bytes) VALUES(?,?,?,?,?,?,?)",(operation_id,actor_id,item.scope,item.scope_key,item.ram_bytes,item.cpu_cores,item.disk_bytes))
            connection.commit(); return operation_id, True
        except Exception:
            connection.rollback(); raise
        finally: connection.close()
    def finish(self, operation_id:str, *, status:str, error_code:str|None=None) -> None:
        if status not in {"succeeded","failed","unknown"}: raise ValueError("invalid terminal status")
        connection=connect(self._path)
        try:
            with connection:
                connection.execute("UPDATE operations SET status=?, error_code=?, completed_at=? WHERE id=? AND status IN ('pending','running')",(status,error_code,_now(),operation_id))
                if status in {"succeeded","failed"}: connection.execute("DELETE FROM allocation_reservations WHERE operation_id=?",(operation_id,))
        finally: connection.close()
