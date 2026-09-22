import unittest
from types import SimpleNamespace

from hsm.lxd import DiscoveryError, LxdDiscovery


class Response:
    def __init__(self, value): self.value = value
    def raise_for_status(self): pass
    def json(self): return {"metadata": self.value}


def client_for(routes, projects=("good",), failures=()):
    class Endpoint:
        def __init__(self, project, path=""):
            self.project, self.path = project, path
        def __getattr__(self, part):
            return Endpoint(self.project, f"{self.path}/{part}".strip("/"))
        def get(self, **_):
            return Response(routes[(self.project, self.path)])
    def factory(project=None, timeout=None):
        if project in failures:
            raise RuntimeError("connection refused")
        api = Endpoint(project)
        return SimpleNamespace(api=api, projects=SimpleNamespace(all=lambda: [SimpleNamespace(name=name) for name in projects]))
    return factory


class DiscoveryTests(unittest.TestCase):
    def test_inventory_keeps_partial_project_failure_explicit(self):
        def factory(project=None, timeout=None):
            if project == "bad": return SimpleNamespace(api=SimpleNamespace(instances=SimpleNamespace(get=lambda **_: (_ for _ in ()).throw(RuntimeError("malformed")))))
            if project == "good": return SimpleNamespace(api=SimpleNamespace(instances=SimpleNamespace(get=lambda **_: Response([{ "name":"x", "status":"RUNNING", "config":{"volatile.uuid":"uuid"}, "expanded_config":{}, "expanded_devices":{}}]))))
            return SimpleNamespace(projects=SimpleNamespace(all=lambda: [SimpleNamespace(name="good"), SimpleNamespace(name="bad")]))
        result=LxdDiscovery(factory, 3).inventory()
        self.assertEqual(result["instances"][0]["lxd_uuid"], "uuid")
        self.assertEqual(result["partial"][0]["project"], "bad")

    def test_unavailable_root_raises_safe_error(self):
        with self.assertRaises(DiscoveryError) as error:
            LxdDiscovery(lambda **_: (_ for _ in ()).throw(RuntimeError("connection refused")), 3).inventory()
        self.assertEqual(error.exception.kind, "unavailable")

    def test_typed_capacity_options_use_injected_client(self):
        routes = {
            (None, ""): {"environment": {"server_cpu_total": 8, "server_memory_total": 100}},
            (None, "resources"): {"cpu": {"total": 8}, "memory": {"total": 100}},
            (None, "storage_pools"): ["pool-a"],
            (None, "networks"): ["br0"],
            (None, "storage_pools/pool-a"): {"driver": "btrfs", "resources": {"space": {"total": 1000, "used": 250}}},
            (None, "networks/br0"): {"managed": True, "type": "bridge", "config": {"ipv4.address": "auto"}},
            ("good", "images"): [{"fingerprint": "abc", "aliases": [{"name": "ubuntu:24.04"}]}],
            ("good", "profiles"): ["default"],
            ("good", "profiles/default"): {"config": {}, "devices": {"root": {"type": "disk", "path": "/", "pool": "pool-a"}, "eth0": {"type": "nic", "network": "br0"}}},
        }
        result = LxdDiscovery(client_for(routes), 3).capacity("good")
        self.assertEqual(result["pools"][0]["free_bytes"], 750)
        self.assertTrue(result["pools"][0]["quota_supported"])
        self.assertEqual(result["images"][0]["alias"], "ubuntu:24.04")
        self.assertTrue(result["profiles"][0]["safe"])

    def test_malformed_pool_is_safe_error(self):
        routes = {(None, "storage_pools"): ["bad"], (None, "storage_pools/bad"): {"driver": "btrfs", "resources": {"space": {}}}}
        with self.assertRaises(DiscoveryError) as error:
            LxdDiscovery(client_for(routes), 3).pools()
        self.assertEqual(error.exception.kind, "malformed")

    def test_expanded_collection_objects_are_supported(self):
        routes = {(None, "storage_pools"): [{"name": "pool", "driver": "btrfs", "resources": {"space": {"total": 20, "used": 5}}}], (None, "networks"): [{"name": "br", "managed": True, "type": "bridge", "config": {}}], ("good", "profiles"): [{"name": "safe", "config": {}, "devices": {"root": {"type": "disk", "path": "/", "pool": "pool"}, "eth0": {"type": "nic", "network": "br"}}}]}
        discovery = LxdDiscovery(client_for(routes), 3)
        self.assertEqual(discovery.pools()[0].free_bytes, 15)
        self.assertEqual(discovery.networks()[0].name, "br")
        self.assertTrue(discovery.profiles("good")[0].safe)

    def test_shared_image_is_a_server_derived_option(self):
        routes = {("good", "images"): [], (None, "images"): [{"fingerprint": "fp", "aliases": [], "properties": {"os": "ubuntu", "version": "24.04"}}]}
        image = LxdDiscovery(client_for(routes), 3).image_aliases("good")[0]
        self.assertEqual((image.alias, image.fingerprint), ("ubuntu:24.04", "fp"))

    def test_unsafe_profile_is_not_approved(self):
        routes = {("good", "profiles"): ["unsafe"], ("good", "profiles/unsafe"): {"config": {"security.privileged": "true"}, "devices": {}}}
        profile = LxdDiscovery(client_for(routes), 3).profiles("good")[0]
        self.assertFalse(profile.safe)
        self.assertIn("unsafe", profile.reason)

    def test_unapproved_profile_device_is_not_approved(self):
        routes = {("good", "profiles"): ["unsafe"], ("good", "profiles/unsafe"): {"config": {}, "devices": {"host": {"type": "disk", "source": "/etc", "path": "/mnt"}}}}
        self.assertFalse(LxdDiscovery(client_for(routes), 3).profiles("good")[0].safe)
