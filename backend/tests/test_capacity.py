from pathlib import Path
import tempfile, unittest
from hsm.db import connect, migrate
from hsm.lxd.capacity import CapacityService
class Fake:
 def inventory(self): return {"instances":[],"partial":[{"project":"hsm","error":"timeout"}]}
 def capacity(self, project): return {"host":{"cpu_total":4,"memory_total":100},"pools":[],"networks":[],"images":[],"profiles":[]}
class CapacityTests(unittest.TestCase):
 def test_partial_inventory_is_not_feasible(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/"x"; migrate(p); c=connect(p); c.execute("INSERT INTO users(id,email,role,status,quota_ram_bytes,quota_cpu_cores,quota_disk_bytes,created_at) VALUES(1,'a@b','admin','active',10,2,10,'x')"); c.commit();c.close()
   result=CapacityService(p,Fake()).get(1)
  self.assertFalse(result["feasible"]); self.assertIn("inventory_partial",result["constraints"])

 def test_safe_discovered_options_and_bounds_are_returned(self):
  class Complete:
   def inventory(self): return {"instances":[],"partial":[]}
   def capacity(self, project): return {"host":{"cpu_total":20,"memory_total":1000},"pools":[{"name":"pool","free_bytes":800,"quota_supported":True}],"networks":[{"name":"bridge","managed":True,"type":"bridge"}],"images":[{"alias":"image","fingerprint":"x"}],"profiles":[{"name":"safe","safe":True}]}
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/"x"; migrate(p); c=connect(p); c.execute("INSERT INTO users(id,email,role,status,quota_ram_bytes,quota_cpu_cores,quota_disk_bytes,created_at) VALUES(1,'a@b','admin','active',500,10,700,'x')"); c.commit(); c.close()
   result=CapacityService(p,Complete()).get(1)
  self.assertTrue(result["feasible"]); self.assertEqual(result["bounds"]["disk_bytes"]["max"],700); self.assertEqual(result["networks"][0]["name"],"bridge")
