"""Narrow LXD creation adapter.  It accepts only a server-built payload."""
from __future__ import annotations

from typing import Any, Callable
import time

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

    def execute(self, project: str, name: str, command: str) -> dict[str, object]:
        chunks: list[tuple[str, bytes]]=[]; size=0; truncated=False
        def handler(kind: str):
            def receive(data: str | bytes) -> None:
                nonlocal size,truncated
                raw=data.encode() if isinstance(data,str) else data
                room=max(0,65536-size); chunks.append((kind,raw[:room]));size+=min(len(raw),room);truncated|=len(raw)>room
            return receive
        started=time.monotonic()
        try:
            instance=self._factory(project=project,timeout=self._timeout).instances.get(name)
            result=instance.execute(["/usr/bin/timeout","-k","2s","15s","/bin/sh","-c",command],environment={"PATH":"/usr/sbin:/usr/bin:/sbin:/bin","LANG":"C"},stdin_payload=None,stdout_handler=handler("out"),stderr_handler=handler("err"),decode=False)
            out=b"".join(v for k,v in chunks if k=="out").decode("utf-8","replace");err=b"".join(v for k,v in chunks if k=="err").decode("utf-8","replace")
            return {"stdout":out,"stderr":err,"exit_code":result.exit_code,"duration_ms":int((time.monotonic()-started)*1000),"truncated":truncated,"timed_out":result.exit_code==124}
        except Exception as error:
            raise DiscoveryError("unavailable" if "connection" in str(error).lower() else "malformed","LXD exec outcome could not be confirmed") from error
