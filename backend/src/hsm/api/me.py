"""Authenticated caller identity and server-calculated quota data."""

from __future__ import annotations

from pathlib import Path

import falcon

from hsm.db import connect
from hsm.quota import quota_for_user, usage_for_user


class MeResource:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def on_get(self, request: falcon.Request, response: falcon.Response) -> None:
        connection = connect(self._database_path)
        try:
            quota = quota_for_user(connection, request.context.user.id)
            allocated = usage_for_user(connection, request.context.user.id)
        finally:
            connection.close()
        response.media = {
            "id": request.context.user.id, "email": request.context.user.email,
            "role": request.context.user.role, "quota": quota.__dict__, "allocated": allocated.__dict__,
        }


class MyQuotaResource:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def on_get(self, request: falcon.Request, response: falcon.Response) -> None:
        connection = connect(self._database_path)
        try:
            quota = quota_for_user(connection, request.context.user.id)
            allocated = usage_for_user(connection, request.context.user.id)
        finally:
            connection.close()
        response.media = {
            "quota": quota.__dict__, "allocated": allocated.__dict__, "reserved": {
                "ram_bytes": 0, "cpu_cores": 0, "disk_bytes": 0,
            }, "remaining": {
                "ram_bytes": quota.ram_bytes - allocated.ram_bytes,
                "cpu_cores": quota.cpu_cores - allocated.cpu_cores,
                "disk_bytes": quota.disk_bytes - allocated.disk_bytes,
            }, "shared_charge_policy": "configured allocation is charged once per owned-or-assigned container",
        }
