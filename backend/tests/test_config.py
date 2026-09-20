import unittest

from hsm.config import ConfigurationError, load_settings


def valid_environment() -> dict[str, str]:
    return {
        "PUBLIC_BASE_URL": "http://localhost:8000",
        "GOOGLE_CLIENT_ID": "local-client-id",
        "GOOGLE_CLIENT_SECRET": "local-client-secret",
        "BOOTSTRAP_ADMIN_EMAIL": "admin@example.test",
        "COOKIE_SECURE": "false",
    }


class LoadSettingsTests(unittest.TestCase):
    def test_derives_local_callback_and_applies_typed_defaults(self) -> None:
        settings = load_settings(valid_environment())
        self.assertEqual(settings.google_redirect_uri, "http://localhost:8000/auth/callback")
        self.assertEqual(settings.collector_interval_seconds, 10)
        self.assertFalse(settings.cookie_secure)

    def test_rejects_non_loopback_http(self) -> None:
        environment = valid_environment() | {"PUBLIC_BASE_URL": "http://example.test"}
        with self.assertRaises(ConfigurationError):
            load_settings(environment)

    def test_rejects_collector_cadence_change(self) -> None:
        environment = valid_environment() | {"COLLECTOR_INTERVAL_SECONDS": "11"}
        with self.assertRaises(ConfigurationError):
            load_settings(environment)
