"""Strict admin-only invitation, quota, role, and assignment endpoints."""

from __future__ import annotations

from pathlib import Path

import falcon

from hsm.quota import Allocation, QuotaError, usage_for_user
from hsm.db import connect
from hsm.users import UserConflict, UserService


def _body(request: falcon.Request, allowed: set[str], required: set[str] = set()) -> dict[str, object]:
    if request.content_type != "application/json":
        raise falcon.HTTPUnsupportedMediaType(description="Content-Type must be application/json")
    if request.content_length is not None and request.content_length > 16384:
        raise falcon.HTTPPayloadTooLarge(description="Request body is too large")
    try:
        payload = request.media
    except Exception as error:
        raise falcon.HTTPBadRequest(description="Request body must be valid JSON") from error
    if not isinstance(payload, dict) or set(payload) - allowed or required - set(payload):
        raise falcon.HTTPBadRequest(description="Request body has missing or unknown fields")
    return payload


def _integer(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise falcon.HTTPBadRequest(description=f"{field} must be a non-negative integer")
    return value


def _quota(value: object) -> Allocation:
    if not isinstance(value, dict) or set(value) != {"ram_bytes", "cpu_cores", "disk_bytes"}:
        raise falcon.HTTPBadRequest(description="quota must contain ram_bytes, cpu_cores, and disk_bytes")
    return Allocation(
        _integer(value["ram_bytes"], "quota.ram_bytes"),
        _integer(value["cpu_cores"], "quota.cpu_cores"),
        _integer(value["disk_bytes"], "quota.disk_bytes"),
    )


def _user_id(request: falcon.Request) -> int:
    raw = request.context.route_params.get("user_id", "")
    if not raw.isdecimal() or int(raw) < 1:
        raise falcon.HTTPNotFound(description="User not found")
    return int(raw)


def _error(error: Exception) -> None:
    if isinstance(error, QuotaError):
        raise falcon.HTTPConflict(
            title="Quota exceeded",
            description=str(error),
        ) from error
    if isinstance(error, UserConflict):
        raise falcon.HTTPConflict(description=str(error)) from error
    if isinstance(error, LookupError):
        raise falcon.HTTPNotFound(description=str(error)) from error
    raise error


class UsersResource:
    def __init__(self, database_path: Path) -> None:
        self._service = UserService(database_path)
        self._database_path = database_path

    def on_get(self, request: falcon.Request, response: falcon.Response) -> None:
        items = []
        connection = connect(self._database_path)
        try:
            for user in self._service.list_users():
                usage = usage_for_user(connection, user.id)
                items.append({
                    "id": user.id, "email": user.email, "role": user.role, "status": user.status,
                    "quota": user.quota.__dict__, "allocated": usage.__dict__,
                })
        finally:
            connection.close()
        response.media = {"items": items, "next_cursor": None}

    def on_post(self, request: falcon.Request, response: falcon.Response) -> None:
        payload = _body(request, {"email", "role", "quota"}, {"email", "role", "quota"})
        if not isinstance(payload["email"], str) or payload["role"] not in {"admin", "user"}:
            raise falcon.HTTPBadRequest(description="email and role are invalid")
        try:
            invited = self._service.invite(
                email=payload["email"], role=payload["role"], quota=_quota(payload["quota"]),
                invited_by=request.context.user.id,
            )
        except Exception as error:
            _error(error)
        response.status = falcon.HTTP_201
        response.media = {"id": invited.id, "email": invited.email, "role": invited.role, "status": invited.status}


class UserResource:
    def __init__(self, database_path: Path) -> None:
        self._service = UserService(database_path)

    def on_patch(self, request: falcon.Request, response: falcon.Response, **params: str) -> None:
        payload = _body(request, {"role", "quota"})
        if not payload:
            raise falcon.HTTPBadRequest(description="Request body cannot be empty")
        role = payload.get("role")
        if role is not None and role not in {"admin", "user"}:
            raise falcon.HTTPBadRequest(description="role must be admin or user")
        try:
            self._service.update(user_id=_user_id(request), role=role, quota=_quota(payload["quota"]) if "quota" in payload else None)
        except Exception as error:
            _error(error)
        response.status = falcon.HTTP_204

    def on_delete(self, request: falcon.Request, response: falcon.Response, **params: str) -> None:
        try:
            self._service.revoke(user_id=_user_id(request))
        except Exception as error:
            _error(error)
        response.status = falcon.HTTP_204


class AssignmentResource:
    def __init__(self, database_path: Path) -> None:
        self._service = UserService(database_path)

    def on_put(self, request: falcon.Request, response: falcon.Response, **params: str) -> None:
        payload = _body(request, set())
        if payload:
            raise falcon.HTTPBadRequest(description="Request body must be empty")
        try:
            self._service.assign(
                user_id=_user_id(request), container_id=request.context.route_params["container_id"],
                assigned_by=request.context.user.id,
            )
        except Exception as error:
            _error(error)
        response.status = falcon.HTTP_204

    def on_delete(self, request: falcon.Request, response: falcon.Response, **params: str) -> None:
        self._service.unassign(
            user_id=_user_id(request), container_id=request.context.route_params["container_id"]
        )
        response.status = falcon.HTTP_204
