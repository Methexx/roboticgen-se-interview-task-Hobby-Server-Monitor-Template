import unittest
import falcon
from hsm.api.containers import CreateContainerResource


class CreationValidationTests(unittest.TestCase):
    def payload(self):
        return {"name":"hsm-disposable-test","image":"image","pool":"pool","network":"bridge","profile":"safe","ram_bytes":100,"cpu_cores":1,"cpu_allowance_pct":50,"disk_bytes":100,"process_limit":100,"autostart":False,"ephemeral":True,"start":False,"description":"test"}

    def capacity(self):
        return {"images":[{"alias":"image","fingerprint":"fp"}],"pools":[{"name":"pool","available_bytes":100}],"networks":[{"name":"bridge"}],"profiles":[{"name":"safe","root_pool":"pool","network":"bridge"}],"bounds":{"ram_bytes":{"max":100},"cpu_cores":{"max":1},"disk_bytes":{"max":100}}}

    def test_rejects_unsafe_or_unknown_options(self):
        payload = self.payload(); payload["network"] = "host0"
        with self.assertRaises(falcon.HTTPError):
            CreateContainerResource._validate_options(payload, self.capacity())

    def test_rejects_raw_or_invalid_name(self):
        payload = self.payload(); payload["name"] = "../unsafe"
        with self.assertRaises(falcon.HTTPError):
            CreateContainerResource._validate(payload)

    def test_rejects_trailing_hyphen_name(self):
        payload = self.payload(); payload["name"] = "hsm-disposable-"
        with self.assertRaises(falcon.HTTPError):
            CreateContainerResource._validate(payload)

    def test_accepts_server_bounded_payload(self):
        CreateContainerResource._validate(self.payload())
        CreateContainerResource._validate_options(self.payload(), self.capacity())
