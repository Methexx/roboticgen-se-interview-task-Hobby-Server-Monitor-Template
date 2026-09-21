"""Read-only, project-aware LXD discovery with no mutation methods."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable

@dataclass(frozen=True)
class DiscoveryError(Exception):
    kind: str
    message: str

class LxdDiscovery:
    def __init__(self, client_factory: Callable[..., Any], timeout_seconds: int) -> None:
        self._client_factory, self._timeout = client_factory, timeout_seconds

    def inventory(self) -> dict[str, object]:
        try:
            root = self._client_factory(timeout=self._timeout)
            projects = [p.name for p in root.projects.all()]
            results, partial = [], []
            for project in projects:
                try:
                    response = self._client_factory(project=project, timeout=self._timeout).api.instances.get(params={"recursion": 1})
                    response.raise_for_status()
                    metadata = response.json().get("metadata")
                    if not isinstance(metadata, list): raise ValueError("instances metadata is not a list")
                    for item in metadata:
                        config = item.get("config", {})
                        uuid = config.get("volatile.uuid")
                        if not isinstance(uuid, str) or not uuid: raise ValueError("instance UUID is missing")
                        results.append({"project": project, "lxd_uuid": uuid, "name": item.get("name"), "status": item.get("status")})
                except Exception as error:
                    partial.append({"project": project, "error": self._kind(error)})
            return {"projects": projects, "instances": results, "partial": partial}
        except Exception as error:
            raise DiscoveryError(self._kind(error), "LXD inventory is unavailable") from error

    def capacity(self) -> dict[str, object]:
        try:
            root = self._client_factory(timeout=self._timeout)
            server = root.api.get(); server.raise_for_status(); metadata = server.json().get("metadata", {})
            environment = metadata.get("environment", {})
            pools = root.api.storage_pools.get(); pools.raise_for_status()
            networks = root.api.networks.get(); networks.raise_for_status()
            return {"host": {"cpu_total": environment.get("server_cpu_total"), "memory_total": environment.get("server_memory_total")}, "pools": pools.json().get("metadata", []), "networks": networks.json().get("metadata", []), "images": [], "profiles": []}
        except Exception as error:
            raise DiscoveryError(self._kind(error), "LXD capacity is unavailable") from error

    @staticmethod
    def _kind(error: Exception) -> str:
        text = str(error).lower()
        if "timeout" in text: return "timeout"
        if "connection" in text or "socket" in text or "unavailable" in text: return "unavailable"
        return "malformed"
