"""Typed, injected, read-only discovery of the local LXD daemon."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class DiscoveryError(Exception):
    kind: str
    message: str


@dataclass(frozen=True)
class Pool:
    name: str
    driver: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    quota_supported: bool


@dataclass(frozen=True)
class Network:
    name: str
    managed: bool
    type: str
    config: dict[str, str]


@dataclass(frozen=True)
class ImageAlias:
    alias: str
    fingerprint: str


@dataclass(frozen=True)
class Profile:
    name: str
    safe: bool
    reason: str | None
    root_pool: str | None
    network: str | None


class LxdDiscovery:
    """Read-only LXD access behind an injectable pylxd-client factory."""

    def __init__(self, client_factory: Callable[..., Any], timeout_seconds: int) -> None:
        self._factory = client_factory
        self._timeout = timeout_seconds

    def _client(self, project: str | None = None) -> Any:
        kwargs: dict[str, Any] = {"timeout": self._timeout}
        if project is not None:
            kwargs["project"] = project
        try:
            return self._factory(**kwargs)
        except Exception as error:
            raise DiscoveryError(self._kind(error), "LXD is unavailable") from error

    def _get(self, client: Any, path: str) -> Any:
        try:
            endpoint = client.api
            for part in filter(None, path.split("/")):
                endpoint = getattr(endpoint, part)
            response = endpoint.get(params={"recursion": 1})
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or "metadata" not in payload:
                raise ValueError("missing LXD metadata")
            return payload["metadata"]
        except DiscoveryError:
            raise
        except Exception as error:
            raise DiscoveryError(self._kind(error), "Malformed or unavailable LXD response") from error

    def projects(self) -> list[str]:
        try:
            names = [project.name for project in self._client().projects.all()]
            if not all(isinstance(name, str) and name for name in names):
                raise ValueError("project without a name")
            return names
        except DiscoveryError:
            raise
        except Exception as error:
            raise DiscoveryError(self._kind(error), "LXD project list is unavailable") from error

    def inventory(self) -> dict[str, Any]:
        projects = self.projects()
        instances: list[dict[str, str]] = []
        partial: list[dict[str, str]] = []
        for project in projects:
            try:
                data = self._get(self._client(project), "instances")
                if not isinstance(data, list):
                    raise ValueError("instances is not a list")
                for item in data:
                    if not isinstance(item, dict):
                        raise ValueError("malformed instance")
                    config = item.get("config")
                    identifier = config.get("volatile.uuid") if isinstance(config, dict) else None
                    name, status = item.get("name"), item.get("status")
                    if not all(isinstance(value, str) and value for value in (identifier, name, status)):
                        raise ValueError("instance identity is incomplete")
                    instances.append({"project": project, "lxd_uuid": identifier, "name": name, "status": status})
            except (DiscoveryError, ValueError) as error:
                kind = error.kind if isinstance(error, DiscoveryError) else "malformed"
                partial.append({"project": project, "error": kind})
        return {"projects": projects, "instances": instances, "partial": partial}

    def pools(self) -> list[Pool]:
        root = self._client()
        names = self._get(root, "storage_pools")
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise DiscoveryError("malformed", "Storage pool list is malformed")
        pools: list[Pool] = []
        for name in names:
            data = self._get(root, f"storage_pools/{name}")
            if not isinstance(data, dict):
                raise DiscoveryError("malformed", "Storage pool detail is malformed")
            space = data.get("resources", {}).get("space", {})
            total, used, driver = space.get("total"), space.get("used"), data.get("driver")
            if not isinstance(driver, str) or not isinstance(total, int) or not isinstance(used, int) or total < 0 or used < 0:
                raise DiscoveryError("malformed", "Storage pool capacity is unavailable")
            pools.append(Pool(name, driver, total, used, max(0, total - used), driver in {"btrfs", "zfs", "lvm", "ceph"}))
        return pools

    def image_aliases(self, project: str) -> list[ImageAlias]:
        data = self._get(self._client(project), "images")
        if not isinstance(data, list):
            raise DiscoveryError("malformed", "Image list is malformed")
        aliases: list[ImageAlias] = []
        for image in data:
            if not isinstance(image, dict) or not isinstance(image.get("fingerprint"), str):
                raise DiscoveryError("malformed", "Image detail is malformed")
            for alias in image.get("aliases", []):
                name = alias.get("name") if isinstance(alias, dict) else None
                if isinstance(name, str) and name:
                    aliases.append(ImageAlias(name, image["fingerprint"]))
        return aliases

    def networks(self) -> list[Network]:
        root = self._client()
        names = self._get(root, "networks")
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise DiscoveryError("malformed", "Network list is malformed")
        networks: list[Network] = []
        for name in names:
            data = self._get(root, f"networks/{name}")
            config = data.get("config") if isinstance(data, dict) else None
            if not isinstance(config, dict):
                raise DiscoveryError("malformed", "Network detail is malformed")
            networks.append(Network(name, data.get("managed") is True, str(data.get("type", "")), {str(k): str(v) for k, v in config.items()}))
        return networks

    def profiles(self, project: str) -> list[Profile]:
        root = self._client(project)
        names = self._get(root, "profiles")
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise DiscoveryError("malformed", "Profile list is malformed")
        return [self._profile(name, self._get(root, f"profiles/{name}")) for name in names]

    @staticmethod
    def _profile(name: str, data: Any) -> Profile:
        if not isinstance(data, dict) or not isinstance(data.get("config"), dict) or not isinstance(data.get("devices"), dict):
            return Profile(name, False, "profile detail is malformed", None, None)
        config, devices = data["config"], data["devices"]
        if any(key.startswith("raw.") for key in config) or config.get("security.privileged") == "true" or config.get("security.nesting") == "true":
            return Profile(name, False, "profile contains unsafe configuration", None, None)
        root_pool: str | None = None
        network: str | None = None
        for device in devices.values():
            if not isinstance(device, dict):
                return Profile(name, False, "profile device is malformed", None, None)
            kind = device.get("type")
            if kind == "disk" and device.get("path") == "/" and isinstance(device.get("pool"), str):
                root_pool = device["pool"]
            elif kind == "nic" and device.get("nictype", "bridged") == "bridged" and isinstance(device.get("network"), str):
                network = device["network"]
            else:
                return Profile(name, False, "profile contains an unapproved device", None, None)
        if root_pool is None or network is None:
            return Profile(name, False, "profile lacks safe root disk or network", None, None)
        return Profile(name, True, None, root_pool, network)

    def capacity(self, creation_project: str | None = None) -> dict[str, Any]:
        root = self._client()
        server = self._get(root, "")
        environment = server.get("environment", {}) if isinstance(server, dict) else {}
        if not isinstance(environment, dict):
            raise DiscoveryError("malformed", "Host environment is malformed")
        payload: dict[str, Any] = {
            "host": {"cpu_total": environment.get("server_cpu_total"), "memory_total": environment.get("server_memory_total")},
            "pools": [asdict(pool) for pool in self.pools()],
            "networks": [asdict(network) for network in self.networks()],
            "images": [], "profiles": [],
        }
        if creation_project:
            payload["images"] = [asdict(image) for image in self.image_aliases(creation_project)]
            payload["profiles"] = [asdict(profile) for profile in self.profiles(creation_project)]
        return payload

    @staticmethod
    def _kind(error: Exception) -> str:
        text = str(error).lower()
        if "timeout" in text:
            return "timeout"
        if any(word in text for word in ("connection", "socket", "refused", "unavailable")):
            return "unavailable"
        return "malformed"
