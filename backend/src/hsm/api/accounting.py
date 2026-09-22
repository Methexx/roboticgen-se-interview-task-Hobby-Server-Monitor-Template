"""SQLite accounting views; no API-side TinyFlux access."""
from __future__ import annotations
from pathlib import Path
import falcon
from hsm.db import connect
from hsm.quota import quota_for_user, usage_for_user

class AccountingResource:
 def __init__(self,path:Path): self.path=path
 def on_get(self,request:falcon.Request,response:falcon.Response)->None:
  c=connect(self.path)
  try:
   if request.context.user.role!="admin":
    q,u=quota_for_user(c,request.context.user.id),usage_for_user(c,request.context.user.id);response.media={"quota":q.__dict__,"allocated":u.__dict__};return
   host=c.execute("SELECT COALESCE(SUM(ram_bytes),0),COALESCE(SUM(cpu_cores),0),COALESCE(SUM(disk_bytes),0) FROM container_allocations a JOIN containers c ON c.id=a.container_id WHERE c.lifecycle='present'").fetchone()
   users=[]
   for row in c.execute("SELECT id,email,quota_ram_bytes,quota_cpu_cores,quota_disk_bytes FROM users WHERE status='active' ORDER BY email"):
    u=usage_for_user(c,row[0]);users.append({"id":row[0],"email":row[1],"quota":{"ram_bytes":row[2],"cpu_cores":row[3],"disk_bytes":row[4]},"allocated":u.__dict__})
   response.media={"host_allocated":{"ram_bytes":host[0],"cpu_cores":host[1],"disk_bytes":host[2]},"users":users}
  finally:c.close()
