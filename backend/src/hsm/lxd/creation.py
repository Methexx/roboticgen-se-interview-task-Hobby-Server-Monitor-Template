"""Narrow LXD creation adapter.  It accepts only a server-built payload."""
from __future__ import annotations

from typing import Any, Callable

from hsm.lxd.discovery import DiscoveryError


class LxdCreator:
    def __init__(self, client_factory: Callable[..., Any], timeout_seconds: int) -> None:
        self._factory, self._timeout = client_factory, timeout_seconds

    def create(self, project: str, payload: dict[str, object], start: bool) -> dict[str, str]:
        try:
            instance = self._factory(project=project, timeout=self._timeout).instances.create(payload, wait=True)
            if start:
                instance.start(wait=True)
            identifier = instance.config.get("volatile.uuid")
            if not isinstance(identifier, str) or not identifier:
                raise ValueError("LXD did not return an instance UUID")
            return {"lxd_uuid": identifier, "name": instance.name}
        except Exception as error:
            text = str(error).lower()
            kind = "timeout" if "timeout" in text else "unavailable" if any(x in text for x in ("connection", "socket", "refused")) else "malformed"
            raise DiscoveryError(kind, "LXD create outcome could not be confirmed") from error

    def action(self, project: str, name: str, action: str) -> None:
        try:
            instance = self._factory(project=project, timeout=self._timeout).instances.get(name)
            if action == "delete": instance.delete(wait=True)
            else: getattr(instance, action)(wait=True)
        except Exception as error:
            text = str(error).lower()
            kind = "timeout" if "timeout" in text else "unavailable" if any(x in text for x in ("connection", "socket", "refused")) else "malformed"
            raise DiscoveryError(kind, "LXD mutation outcome could not be confirmed") from error

    def update_limits(self, project: str, name: str, config: dict[str, str], disk_bytes: int | None) -> None:
        try:
            instance = self._factory(project=project, timeout=self._timeout).instances.get(name)
            instance.config.update(config)
            if disk_bytes is not None:
                root = instance.devices.get("root")
                if not isinstance(root, dict) or root.get("type") != "disk" or root.get("path") != "/":
                    raise ValueError("safe root disk is unavailable")
                root["size"] = str(disk_bytes)
                instance.devices["root"] = root
            instance.save(wait=True)
        except Exception as error:
            text = str(error).lower()
            kind = "timeout" if "timeout" in text else "unavailable" if any(x in text for x in ("connection", "socket", "refused")) else "malformed"
            raise DiscoveryError(kind, "LXD limit update outcome could not be confirmed") from error
