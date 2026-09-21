import falcon.testing
import unittest

from hsm.app import HealthResource, create_app
from hsm.authz.policy import Policy, PolicyRegistry
from hsm.config import load_settings


def settings():
    return load_settings({
        "PUBLIC_BASE_URL": "http://localhost:8000",
        "GOOGLE_CLIENT_ID": "test-client",
        "GOOGLE_CLIENT_SECRET": "test-secret",
        "BOOTSTRAP_ADMIN_EMAIL": "admin@example.test",
        "COOKIE_SECURE": "false",
    })


class ApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app(settings())
        self.client = falcon.testing.TestClient(self.app)

    def test_health_is_the_only_public_endpoint(self) -> None:
        response = self.client.simulate_get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"ok": True})
        self.assertRegex(response.headers["x-request-id"], r"^[0-9a-f-]{36}$")

    def test_undeclared_path_is_denied_with_structured_error(self) -> None:
        response = self.client.simulate_get("/api/not-registered")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json["error"]["code"], "not_found")
        self.assertIn("request_id", response.json)

    def test_undeclared_method_is_denied(self) -> None:
        response = self.client.simulate_post("/healthz")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json["error"]["code"], "method_not_allowed")


class PolicyRegistryTests(unittest.TestCase):
    def test_startup_rejects_responder_without_policy(self) -> None:
        registry = PolicyRegistry()
        app = falcon.App()
        with self.assertRaises(ValueError):
            registry.add_route(app, "/healthz", HealthResource(), {})

    def test_registry_exposes_each_route_and_method_policy(self) -> None:
        registry = PolicyRegistry()
        app = falcon.App()
        registry.add_route(app, "/healthz", HealthResource(), {"GET": Policy.PUBLIC})
        self.assertEqual(registry.policies[0].policy, Policy.PUBLIC)
        self.assertEqual(registry.policies[0].method, "GET")
