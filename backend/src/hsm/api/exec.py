"""Bounded one-shot commands inside centrally authorized containers."""
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timedelta, timezone
import falcon, json, uuid, hashlib
from hsm.db import connect
from hsm.lxd.creation import LxdCreator
from hsm.lxd.discovery import DiscoveryError

class ExecResource:
 def __init__(self,path:Path,creator:LxdCreator): self.path,self.creator=path,creator
 def on_post(self,request:falcon.Request,response:falcon.Response,**params:str)->None:
  if request.content_type!='application/json' or not isinstance(request.media,dict) or set(request.media)!={'command'} or not isinstance(request.media['command'],str): raise falcon.HTTPBadRequest(description='command is required')
  command=request.media['command']
  if not command or len(command)>2000 or '\x00' in command: raise falcon.HTTPBadRequest(description='command is invalid')
  c=connect(self.path)
  try: row=c.execute("SELECT project,current_name FROM containers WHERE id=? AND lifecycle='present'",(request.context.route_params['container_id'],)).fetchone()
  finally:c.close()
  if row is None: raise falcon.HTTPNotFound(description='Container not found')
  operation_id=str(uuid.uuid4());now=datetime.now(timezone.utc);deadline=now+timedelta(seconds=20)
  c=connect(self.path)
  try:
   c.execute('BEGIN IMMEDIATE');c.execute("DELETE FROM execution_slots WHERE deadline_at<?",(now.isoformat(),))
   if c.execute("SELECT COUNT(*) FROM execution_slots").fetchone()[0]>=4: raise falcon.HTTPTooManyRequests(description='Global execution limit reached')
   c.execute("INSERT INTO operations(id,actor_user_id,kind,target_id,request_hash,status,created_at,deadline_at) VALUES(?,?,?,?,?,'running',?,?)",(operation_id,request.context.user.id,'container.exec',request.context.route_params['container_id'],hashlib.sha256(command.encode()).hexdigest(),now.isoformat(),deadline.isoformat()))
   c.execute("INSERT INTO execution_slots(operation_id,user_id,acquired_at,deadline_at) VALUES(?,?,?,?)",(operation_id,request.context.user.id,now.isoformat(),deadline.isoformat()));c.commit()
  except Exception:
   c.rollback();raise
  finally:c.close()
  try: result=self.creator.execute(row[0],row[1],command)
  except DiscoveryError as error: raise falcon.HTTPServiceUnavailable(description=error.message) from error
  finally:
   c=connect(self.path)
   try:
    with c:c.execute("DELETE FROM execution_slots WHERE operation_id=?",(operation_id,))
   finally:c.close()
  c=connect(self.path)
  try:
   with c:c.execute("UPDATE operations SET status='succeeded',completed_at=datetime('now') WHERE id=?",(operation_id,));c.execute("INSERT INTO audit_log(ts,actor_user_id,action,target_type,target_id,target_name,operation_id,detail_json,outcome) VALUES(datetime('now'),?,?,?,?,?,?,?,?)",(request.context.user.id,'container.exec','container',request.context.route_params['container_id'],row[1],operation_id,json.dumps({'duration_ms':result['duration_ms'],'exit_code':result['exit_code'],'timed_out':result['timed_out'],'truncated':result['truncated']}),'ok'))
  finally:c.close()
  response.media=result
