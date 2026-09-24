"""Bounded in-container execution with durable slots and metadata-only audit."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import uuid

import falcon
from hsm.db import connect
from hsm.lxd.creation import LxdCreator
from hsm.lxd.discovery import DiscoveryError


class ExecResource:
    def __init__(self, path: Path, creator: LxdCreator):
        self.path, self.creator = path, creator

    def on_post(self, request, response, **params):
        if request.content_length is not None and request.content_length > 16384:
            raise falcon.HTTPPayloadTooLarge(description="Command body is too large")
        if request.content_type != 'application/json' or not isinstance(request.media, dict) or set(request.media) != {'command'}:
            raise falcon.HTTPBadRequest(description='command is required')
        command = request.media['command']
        if not isinstance(command, str) or not command or len(command) > 2000 or '\x00' in command:
            raise falcon.HTTPBadRequest(description='command is invalid')
        identifier = request.context.route_params['container_id']
        actor = request.context.user.id
        operation_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        deadline = now + timedelta(seconds=30)
        c = connect(self.path)
        try:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute("SELECT project,current_name,lxd_uuid FROM containers WHERE id=? AND lifecycle='present'", (identifier,)).fetchone()
            if row is None:
                raise falcon.HTTPNotFound(description='Container not found')
            expired = c.execute('SELECT operation_id FROM execution_slots WHERE deadline_at<?', (now.isoformat(),)).fetchall()
            for (old_id,) in expired:
                c.execute("UPDATE operations SET status='unknown',error_code='exec_deadline_expired' WHERE id=? AND status='running'", (old_id,))
            c.execute('DELETE FROM execution_slots WHERE deadline_at<?', (now.isoformat(),))
            if c.execute('SELECT 1 FROM execution_slots WHERE user_id=?', (actor,)).fetchone():
                raise falcon.HTTPTooManyRequests(description='One active execution per user is allowed')
            if c.execute('SELECT COUNT(*) FROM execution_slots').fetchone()[0] >= 4:
                raise falcon.HTTPTooManyRequests(description='Global execution limit reached')
            # Do not persist even a hash of potentially secret-bearing command text.
            c.execute("INSERT INTO operations(id,actor_user_id,kind,target_id,request_hash,status,created_at,deadline_at) VALUES(?,?,?,?,?,'running',?,?)",
                      (operation_id, actor, 'container.exec', identifier, hashlib.sha256(operation_id.encode()).hexdigest(), now.isoformat(), deadline.isoformat()))
            c.execute('INSERT INTO execution_slots(operation_id,user_id,acquired_at,deadline_at) VALUES(?,?,?,?)', (operation_id, actor, now.isoformat(), deadline.isoformat()))
            self._audit(c, actor, identifier, row[1], operation_id, 'intent', {})
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
        try:
            result = self.creator.execute(row[0], row[1], command, expected_uuid=row[2])
            self._finish(operation_id, actor, identifier, row[1], 'succeeded', 'ok',
                         {key: result[key] for key in ('duration_ms', 'exit_code', 'timed_out', 'truncated')})
        except DiscoveryError as error:
            definite = error.kind in {'not_running', 'identity_changed'}
            self._finish(operation_id, actor, identifier, row[1], 'failed' if definite else 'unknown',
                         'denied' if definite else 'unknown', {'error_code': error.kind})
            if definite:
                raise falcon.HTTPConflict(description=error.message) from error
            raise falcon.HTTPServiceUnavailable(description=error.message) from error
        except Exception:
            self._finish(operation_id, actor, identifier, row[1], 'unknown', 'unknown', {'error_code': 'persistence_or_exec_failure'})
            raise
        finally:
            c = connect(self.path)
            try:
                with c:
                    c.execute('DELETE FROM execution_slots WHERE operation_id=?', (operation_id,))
            finally:
                c.close()
        response.media = result

    @staticmethod
    def _audit(c, actor, identifier, name, operation_id, outcome, detail):
        c.execute("INSERT INTO audit_log(ts,actor_user_id,action,target_type,target_id,target_name,operation_id,detail_json,outcome) VALUES(datetime('now'),?,?,?,?,?,?,?,?)",
                  (actor, 'container.exec', 'container', identifier, name, operation_id, json.dumps(detail), outcome))

    def _finish(self, operation_id, actor, identifier, name, status, outcome, detail):
        c = connect(self.path)
        try:
            with c:
                c.execute("UPDATE operations SET status=?,completed_at=datetime('now'),error_code=? WHERE id=?", (status, detail.get('error_code'), operation_id))
                self._audit(c, actor, identifier, name, operation_id, outcome, detail)
        finally:
            c.close()
