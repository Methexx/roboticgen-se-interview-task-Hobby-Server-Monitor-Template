"""Typed, fail-closed configuration for the future API and collector."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlparse


class ConfigurationError(ValueError):
    """Raised before services start when environment configuration is unsafe."""


@dataclass(frozen=True)
class Settings:
    public_base_url: str
    bind_host: str
    port: int
    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str
    bootstrap_admin_email: str
    database_path: Path
    tinyflux_dir: Path
    static_dir: Path
    lxd_socket: Path
    lxd_create_project: str
    lxd_timeout_seconds: int
    collector_interval_seconds: int
    collector_stale_seconds: int
    metrics_retention_days: int
    metrics_max_bytes: int
    cookie_secure: bool


def _required(env: dict[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ConfigurationError(f"{key} is required")
    return value


def _positive_int(env: dict[str, str], key: str, default: int) -> int:
    raw = env.get(key, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigurationError(f"{key} must be an integer") from error
    if value <= 0:
        raise ConfigurationError(f"{key} must be positive")
    return value


def _bool(env: dict[str, str], key: str, default: bool) -> bool:
    raw = env.get(key, str(default).lower()).strip().lower()
    if raw in {"true", "1"}:
        return True
    if raw in {"false", "0"}:
        return False
    raise ConfigurationError(f"{key} must be true or false")


def _validate_origin(url: str, cookie_secure: bool) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError("PUBLIC_BASE_URL must be an absolute HTTP(S) origin")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ConfigurationError("PUBLIC_BASE_URL must not include a path, query, or fragment")
    is_loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not is_loopback:
        raise ConfigurationError("PUBLIC_BASE_URL must use HTTPS outside loopback development")
    if parsed.scheme == "https" and not cookie_secure:
        raise ConfigurationError("COOKIE_SECURE must be true for HTTPS")


def load_settings(environment: dict[str, str] | None = None) -> Settings:
    """Load every used variable explicitly; never print or return redacted secrets."""
    env = dict(os.environ if environment is None else environment)
    cookie_secure = _bool(env, "COOKIE_SECURE", True)
    public_base_url = _required(env, "PUBLIC_BASE_URL").rstrip("/")
    _validate_origin(public_base_url, cookie_secure)
    redirect_uri = env.get("GOOGLE_REDIRECT_URI", "").strip()
    if not redirect_uri:
        redirect_uri = f"{public_base_url}/auth/callback"
    if not redirect_uri.startswith(f"{public_base_url}/"):
        raise ConfigurationError("GOOGLE_REDIRECT_URI must use PUBLIC_BASE_URL")
    bootstrap_email = _required(env, "BOOTSTRAP_ADMIN_EMAIL").lower()
    if "@" not in bootstrap_email or bootstrap_email.startswith("@"):
        raise ConfigurationError("BOOTSTRAP_ADMIN_EMAIL must be an email address")
    collector_interval = _positive_int(env, "COLLECTOR_INTERVAL_SECONDS", 10)
    if collector_interval != 10:
        raise ConfigurationError("COLLECTOR_INTERVAL_SECONDS must be 10")
    stale_seconds = _positive_int(env, "COLLECTOR_STALE_SECONDS", 30)
    if stale_seconds < collector_interval:
        raise ConfigurationError("COLLECTOR_STALE_SECONDS must be at least the collection interval")
    return Settings(
        public_base_url=public_base_url,
        bind_host=env.get("BIND_HOST", "127.0.0.1"),
        port=_positive_int(env, "PORT", 8000),
        google_client_id=_required(env, "GOOGLE_CLIENT_ID"),
        google_client_secret=_required(env, "GOOGLE_CLIENT_SECRET"),
        google_redirect_uri=redirect_uri,
        bootstrap_admin_email=bootstrap_email,
        database_path=Path(env.get("DATABASE_PATH", "./var/hsm.sqlite")),
        tinyflux_dir=Path(env.get("TINYFLUX_DIR", "./var/metrics")),
        static_dir=Path(env.get("STATIC_DIR", "./frontend/dist")),
        lxd_socket=Path(env.get("LXD_SOCKET", "/var/snap/lxd/common/lxd/unix.socket")),
        lxd_create_project=env.get("LXD_CREATE_PROJECT", "hsm"),
        lxd_timeout_seconds=_positive_int(env, "LXD_TIMEOUT_SECONDS", 10),
        collector_interval_seconds=collector_interval,
        collector_stale_seconds=stale_seconds,
        metrics_retention_days=_positive_int(env, "METRICS_RETENTION_DAYS", 7),
        metrics_max_bytes=_positive_int(env, "METRICS_MAX_BYTES", 268435456),
        cookie_secure=cookie_secure,
    )
