"""Bounded one-shot commands inside centrally authorized containers."""
from __future__ import annotations
from pathlib import Path
import falcon, json
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
  try: result=self.creator.execute(row[0],row[1],command)
  except DiscoveryError as error: raise falcon.HTTPServiceUnavailable(description=error.message) from error
  c=connect(self.path)
  try:
   with c:c.execute("INSERT INTO audit_log(ts,actor_user_id,action,target_type,target_id,target_name,detail_json,outcome) VALUES(datetime('now'),?,?,?,?,?,?,?)",(request.context.user.id,'container.exec','container',request.context.route_params['container_id'],row[1],json.dumps({'duration_ms':result['duration_ms'],'exit_code':result['exit_code'],'timed_out':result['timed_out'],'truncated':result['truncated']}),'ok'))
  finally:c.close()
  response.media=result
