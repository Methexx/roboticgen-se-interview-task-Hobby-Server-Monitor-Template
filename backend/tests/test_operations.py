from pathlib import Path
import tempfile, unittest
from hsm.db import connect, migrate
from hsm.operations import OperationConflict, OperationService, Reservation
class OperationTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/"h.sqlite"; migrate(self.path)
  c=connect(self.path); c.execute("INSERT INTO users(id,email,role,status,quota_ram_bytes,quota_cpu_cores,quota_disk_bytes,created_at) VALUES(1,'a@b','admin','active',1,1,1,'x')"); c.commit(); c.close(); self.service=OperationService(self.path)
 def tearDown(self): self.temp.cleanup()
 def test_idempotency_and_uncertain_reservation(self):
  ident,created=self.service.begin(actor_id=1,kind="create",request_hash="a",idempotency_key="key",target_id=None,reservations=(Reservation("user","1",1,1,1),)); self.assertTrue(created)
  self.assertEqual(self.service.begin(actor_id=1,kind="create",request_hash="a",idempotency_key="key",target_id=None)[0],ident)
  with self.assertRaises(OperationConflict): self.service.begin(actor_id=1,kind="create",request_hash="b",idempotency_key="key",target_id=None)
  self.service.finish(ident,status="unknown",error_code="timeout"); c=connect(self.path); self.assertEqual(c.execute("SELECT COUNT(*) FROM allocation_reservations").fetchone()[0],1); c.close()
