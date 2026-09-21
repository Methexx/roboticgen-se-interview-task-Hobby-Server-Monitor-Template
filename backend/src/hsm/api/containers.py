"""SQLite snapshot reads; browser requests never poll LXD for metrics."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import falcon

from hsm.db import connect


def _item(row: sqlite3.Row | tuple[object, ...]) -> dict[str, object]:
    values = tuple(row)
    return {
        "id": values[0], "project": values[1], "name": values[2],
        "state": values[3], "image": values[4], "os_version": values[5],
        "ipv4": json.loads(values[6]) if values[6] else [], "uptime_s": values[7],
        "processes": values[8], "metrics": {
            "cpu_pct": values[9], "ram_used_bytes": values[10], "disk_used_bytes": values[11],
            "net_rx_bytes": values[12], "net_tx_bytes": values[13],
            "net_rx_bps": values[14], "net_tx_bps": values[15],
        }, "sampled_at": values[16], "error_code": values[17],
    }


_SELECT = """SELECT c.id, c.project, c.current_name, m.state, m.image, m.os_version,
                    m.ipv4_json, m.uptime_s, m.processes, m.cpu_pct, m.ram_used_bytes,
                    m.disk_used_bytes, m.net_rx_bytes, m.net_tx_bytes, m.net_rx_bps,
                    m.net_tx_bps, m.sampled_at, m.error_code
             FROM containers AS c LEFT JOIN metrics_latest AS m ON m.container_id = c.id"""


class ContainersResource:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def on_get(self, request: falcon.Request, response: falcon.Response) -> None:
        connection = connect(self._database_path)
        try:
            if request.context.user.role == "admin":
                rows = connection.execute(_SELECT + " WHERE c.lifecycle = 'present' ORDER BY c.project, c.current_name").fetchall()
            else:
                rows = connection.execute(
                    _SELECT + " JOIN container_assignments AS a ON a.container_id = c.id "
                    "WHERE c.lifecycle = 'present' AND a.user_id = ? ORDER BY c.project, c.current_name",
                    (request.context.user.id,),
                ).fetchall()
        finally:
            connection.close()
        response.media = {"items": [_item(row) for row in rows], "next_cursor": None}


class ContainerResource:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def on_get(self, request: falcon.Request, response: falcon.Response, **params: str) -> None:
        connection = connect(self._database_path)
        try:
            row = connection.execute(
                _SELECT + " WHERE c.id = ? AND c.lifecycle = 'present'",
                (request.context.route_params["container_id"],),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise falcon.HTTPNotFound(description="Container not found")
        response.media = _item(row)
