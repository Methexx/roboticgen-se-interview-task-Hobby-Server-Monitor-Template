import unittest
from types import SimpleNamespace

from hsm.lxd import DiscoveryError, LxdDiscovery

class Response:
    def __init__(self, value): self.value=value
    def raise_for_status(self): pass
    def json(self): return {"metadata": self.value}

class DiscoveryTests(unittest.TestCase):
    def test_inventory_keeps_partial_project_failure_explicit(self):
        def factory(project=None, timeout=None):
            if project == "bad": return SimpleNamespace(api=SimpleNamespace(instances=SimpleNamespace(get=lambda **_: (_ for _ in ()).throw(RuntimeError("malformed")))))
            if project == "good": return SimpleNamespace(api=SimpleNamespace(instances=SimpleNamespace(get=lambda **_: Response([{ "name":"x", "status":"RUNNING", "config":{"volatile.uuid":"uuid"}}]))))
            return SimpleNamespace(projects=SimpleNamespace(all=lambda: [SimpleNamespace(name="good"), SimpleNamespace(name="bad")]))
        result=LxdDiscovery(factory, 3).inventory()
        self.assertEqual(result["instances"][0]["lxd_uuid"], "uuid")
        self.assertEqual(result["partial"][0]["project"], "bad")

    def test_unavailable_root_raises_safe_error(self):
        with self.assertRaises(DiscoveryError) as error:
            LxdDiscovery(lambda **_: (_ for _ in ()).throw(RuntimeError("connection refused")), 3).inventory()
        self.assertEqual(error.exception.kind, "unavailable")
