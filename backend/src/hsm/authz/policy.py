"""Explicit per-route, per-method authorization policy registry."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import falcon


class Policy(StrEnum):
    PUBLIC = "public"
    AUTHENTICATED = "authenticated"
    ADMIN = "admin"
    CONTAINER_ACCESS = "container_access"
    OPERATION_OWNER = "operation_owner"


@dataclass(frozen=True)
class RoutePolicy:
    path: str
    method: str
    policy: Policy


class PolicyRegistry:
    """Registers only endpoints whose responders have a declared policy."""

    def __init__(self) -> None:
        self._policies: dict[tuple[str, str], Policy] = {}
        self._paths: dict[str, set[str]] = {}

    @property
    def policies(self) -> tuple[RoutePolicy, ...]:
        return tuple(
            RoutePolicy(path=path, method=method, policy=policy)
            for (path, method), policy in sorted(self._policies.items())
        )

    def add_route(
        self,
        app: falcon.App,
        path: str,
        resource: Any,
        policies: dict[str, Policy],
    ) -> None:
        normalized = {method.upper(): policy for method, policy in policies.items()}
        responder_methods = {
            attribute.removeprefix("on_").upper()
            for attribute in dir(resource)
            if attribute.startswith("on_") and callable(getattr(resource, attribute))
        }
        if responder_methods != set(normalized):
            missing = responder_methods - set(normalized)
            extra = set(normalized) - responder_methods
            raise ValueError(
                f"route {path} policy mismatch: missing={sorted(missing)}, extra={sorted(extra)}"
            )
        for method, policy in normalized.items():
            key = (path, method)
            if key in self._policies:
                raise ValueError(f"duplicate policy for {method} {path}")
            self._policies[key] = policy
            self._paths.setdefault(path, set()).add(method)
        app.add_route(path, resource)

    def authorize(self, request: falcon.Request) -> Policy:
        method = request.method.upper()
        policy = self._policies.get((request.path, method))
        if policy is not None:
            return policy
        methods = self._paths.get(request.path)
        if methods:
            raise falcon.HTTPMethodNotAllowed(allowed_methods=sorted(methods))
        raise falcon.HTTPNotFound()
