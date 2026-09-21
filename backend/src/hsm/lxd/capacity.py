"""Safe read-only capacity calculation and successful inventory reconciliation."""
from __future__ import annotations
from pathlib import Path
import uuid
from hsm.db import connect
from hsm.lxd.discovery import LxdDiscovery, DiscoveryError
from hsm.quota import quota_for_user, usage_for_user

class CapacityService:
 def __init__(self, path:Path, discovery:LxdDiscovery): self.path,self.discovery=path,discovery
 def get(self, user_id:int):
  inventory=self.discovery.inventory(); capacity=self.discovery.capacity()
  if not inventory["partial"]: self._reconcile(inventory["instances"])
  c=connect(self.path)
  try:
   quota,usage=quota_for_user(c,user_id),usage_for_user(c,user_id)
   reserved=c.execute("SELECT COALESCE(SUM(delta_ram_bytes),0),COALESCE(SUM(delta_cpu_cores),0),COALESCE(SUM(delta_disk_bytes),0) FROM allocation_reservations").fetchone()
  finally:c.close()
  host=capacity["host"]; cpu=host.get("cpu_total"); ram=host.get("memory_total")
  constraints=[]
  if inventory["partial"]: constraints.append("inventory_partial")
  if not isinstance(cpu,int) or not isinstance(ram,int): constraints.append("host_capacity_unknown")
  remaining={"ram_bytes":quota.ram_bytes-usage.ram_bytes-reserved[0],"cpu_cores":quota.cpu_cores-usage.cpu_cores-reserved[1],"disk_bytes":quota.disk_bytes-usage.disk_bytes-reserved[2]}
  return {"feasible":not constraints,"constraints":constraints,"host":{"cpu_total":cpu,"memory_total":ram},"pools":capacity["pools"],"networks":capacity["networks"],"images":capacity["images"],"profiles":capacity["profiles"],"bounds":{"ram_bytes":{"max":max(0,remaining["ram_bytes"])},"cpu_cores":{"max":max(0,remaining["cpu_cores"])},"disk_bytes":{"max":max(0,remaining["disk_bytes"])}},"partial":inventory["partial"]}
 def _reconcile(self, instances):
  c=connect(self.path)
  try:
   with c:
    for item in instances:
     ident=str(uuid.uuid5(uuid.NAMESPACE_URL,f"lxd:{item['project']}:{item['lxd_uuid']}"))
     c.execute("""INSERT INTO containers(id,project,lxd_uuid,current_name,managed,isolation_status,lifecycle,first_seen_at,last_seen_at) VALUES(?,?,?,?,0,'unknown','present',datetime('now'),datetime('now')) ON CONFLICT(project,lxd_uuid) DO UPDATE SET current_name=excluded.current_name,last_seen_at=excluded.last_seen_at,lifecycle='present'""",(ident,item["project"],item["lxd_uuid"],item["name"]))
  finally:c.close()
