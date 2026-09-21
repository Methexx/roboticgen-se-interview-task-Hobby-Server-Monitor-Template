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
