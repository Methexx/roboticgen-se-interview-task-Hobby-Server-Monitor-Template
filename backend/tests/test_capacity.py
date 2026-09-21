from pathlib import Path
import tempfile, unittest
from hsm.db import connect, migrate
from hsm.lxd.capacity import CapacityService
class Fake:
 def inventory(self): return {"instances":[],"partial":[{"project":"hsm","error":"timeout"}]}
 def capacity(self): return {"host":{"cpu_total":4,"memory_total":100},"pools":[],"networks":[],"images":[],"profiles":[]}
class CapacityTests(unittest.TestCase):
 def test_partial_inventory_is_not_feasible(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/"x"; migrate(p); c=connect(p); c.execute("INSERT INTO users(id,email,role,status,quota_ram_bytes,quota_cpu_cores,quota_disk_bytes,created_at) VALUES(1,'a@b','admin','active',10,2,10,'x')"); c.commit();c.close()
   result=CapacityService(p,Fake()).get(1)
  self.assertFalse(result["feasible"]); self.assertIn("inventory_partial",result["constraints"])
