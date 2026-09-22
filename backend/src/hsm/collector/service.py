"""SQLite-only collector skeleton; LXD discovery is intentionally absent."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable
import time
import os
from tinyflux import Point, TinyFlux

from hsm.db import connect, migrate


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class LatestSnapshot:
    """A validated collector result for one already-authorized stable container ID."""

    container_id: str
    state: str
    ipv4: tuple[str, ...]
    sampled_at: str
    image: str | None = None
    os_version: str | None = None
    uptime_s: int | None = None
    processes: int | None = None
    cpu_pct: float | None = None
    cpu_core_equivalent: float | None = None
    ram_used_bytes: int | None = None
    disk_used_bytes: int | None = None
    net_rx_bytes: int | None = None
    net_tx_bytes: int | None = None
    net_rx_bps: float | None = None
    net_tx_bps: float | None = None
    error_code: str | None = None


class Collector:
    """Own the future metrics database while keeping API lifetime separate."""

    def __init__(self, database_path: Path, metrics_dir: Path | None = None, retention_hours: int = 168) -> None:
        self._database_path = database_path
        self._metrics_dir = metrics_dir or database_path.parent / "metrics"
        self._retention_hours = retention_hours

    def heartbeat(self, *, now: str | None = None) -> None:
        """Persist that the independent process is alive without contacting LXD."""
        migrate(self._database_path)
        payload = json.dumps({"observed_at": now or _utc_now(), "state": "idle"})
        connection = connect(self._database_path)
        try:
            with connection:
                connection.execute(
                    "INSERT INTO collector_status(key, value_json) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                    ("heartbeat", payload),
                )
        finally:
            connection.close()

    def record_latest(self, snapshot: LatestSnapshot) -> None:
        """Upsert a latest snapshot for an inventory record; no LXD call occurs here."""
        connection = connect(self._database_path)
        values = asdict(snapshot)
        values["ipv4_json"] = json.dumps(values.pop("ipv4"))
        try:
            with connection:
                connection.execute(
                    """INSERT INTO metrics_latest (
                        container_id, state, image, os_version, ipv4_json, uptime_s, processes,
                        cpu_pct, cpu_core_equivalent, ram_used_bytes, disk_used_bytes, net_rx_bytes,
                        net_tx_bytes, net_rx_bps, net_tx_bps, sampled_at, error_code
                    ) VALUES (
                        :container_id, :state, :image, :os_version, :ipv4_json, :uptime_s, :processes,
                        :cpu_pct, :cpu_core_equivalent, :ram_used_bytes, :disk_used_bytes, :net_rx_bytes,
                        :net_tx_bytes, :net_rx_bps, :net_tx_bps, :sampled_at, :error_code
                    ) ON CONFLICT(container_id) DO UPDATE SET
                        state = excluded.state, image = excluded.image, os_version = excluded.os_version,
                        ipv4_json = excluded.ipv4_json, uptime_s = excluded.uptime_s,
                        processes = excluded.processes, cpu_pct = excluded.cpu_pct,
                        cpu_core_equivalent = excluded.cpu_core_equivalent,
                        ram_used_bytes = excluded.ram_used_bytes, disk_used_bytes = excluded.disk_used_bytes,
                        net_rx_bytes = excluded.net_rx_bytes, net_tx_bytes = excluded.net_tx_bytes,
                        net_rx_bps = excluded.net_rx_bps, net_tx_bps = excluded.net_tx_bps,
                        sampled_at = excluded.sampled_at, error_code = excluded.error_code""",
                    values,
                )
                connection.execute("INSERT OR REPLACE INTO metrics_history(container_id,sampled_at,state,cpu_pct,ram_used_bytes,disk_used_bytes,net_rx_bytes,net_tx_bytes) VALUES(?,?,?,?,?,?,?,?)", (snapshot.container_id,snapshot.sampled_at,snapshot.state,snapshot.cpu_pct,snapshot.ram_used_bytes,snapshot.disk_used_bytes,snapshot.net_rx_bytes,snapshot.net_tx_bytes))
        finally:
            connection.close()
        self._record_hourly(snapshot)

    def _record_hourly(self, snapshot: LatestSnapshot) -> None:
        """Collector-only TinyFlux write; an error never discards SQLite latest."""
        try:
            stamp = datetime.fromisoformat(snapshot.sampled_at).astimezone(timezone.utc)
            self._metrics_dir.mkdir(parents=True, exist_ok=True)
            path = self._metrics_dir / f"metrics-{stamp:%Y%m%d%H}.tinyflux"
            fields = {key: value for key, value in asdict(snapshot).items() if key not in {"container_id", "ipv4", "sampled_at", "state", "image", "os_version", "error_code"} and value is not None}
            fields.update({"state": snapshot.state, "ipv4_json": json.dumps(snapshot.ipv4), "image": snapshot.image or "", "os_version": snapshot.os_version or ""})
            with TinyFlux(str(path)) as database:
                database.insert(Point.from_dict({"measurement": "container", "time": snapshot.sampled_at, "tags": {"container_id": snapshot.container_id}, "fields": fields}))
            self._retain()
        except Exception as error:
            self._status({"observed_at": _utc_now(), "state": "tinyflux_error", "error": type(error).__name__})

    def _retain(self) -> None:
        cutoff = time.time() - self._retention_hours * 3600
        for path in self._metrics_dir.glob("metrics-*.tinyflux"):
            if path.stat().st_mtime < cutoff:
                path.unlink()

    def poll_lxd(self, client_factory: Callable[..., Any], timeout_seconds: int) -> None:
        """Read LXD only; retain prior snapshots whenever a project fails."""
        now = _utc_now()
        try:
            root = client_factory(timeout=timeout_seconds)
            projects = [project.name for project in root.projects.all()]
        except Exception as error:
            self._status({"observed_at": now, "state": "lxd_down", "error": type(error).__name__})
            return
        failures: list[str] = []
        for project in projects:
            try:
                client = client_factory(project=project, timeout=timeout_seconds)
                for instance in client.instances.all():
                    identifier = instance.config.get("volatile.uuid")
                    if not identifier: continue
                    connection = connect(self._database_path)
                    try:
                        row = connection.execute("SELECT id FROM containers WHERE project=? AND lxd_uuid=? AND lifecycle='present'", (project, identifier)).fetchone()
                    finally: connection.close()
                    if row is None: continue
                    state = instance.state()
                    network = state.get("network", {}) if isinstance(state, dict) else {}
                    rx = sum(int(data.get("counters", {}).get("bytes_received", 0)) for data in network.values() if isinstance(data, dict))
                    tx = sum(int(data.get("counters", {}).get("bytes_sent", 0)) for data in network.values() if isinstance(data, dict))
                    memory = state.get("memory", {}) if isinstance(state, dict) else {}
                    disk = state.get("disk", {}) if isinstance(state, dict) else {}
                    self.record_latest(LatestSnapshot(container_id=row[0], state=str(state.get("status", "unknown")), ipv4=(), sampled_at=now, ram_used_bytes=memory.get("usage"), disk_used_bytes=sum(int(v.get("usage",0)) for v in disk.values() if isinstance(v,dict)), net_rx_bytes=rx, net_tx_bytes=tx))
            except Exception:
                failures.append(project)
        self._status({"observed_at": now, "state": "partial" if failures else "ok", "project_errors": failures, "last_successful_collection": now if not failures else None})

    def _status(self, payload: dict[str, object]) -> None:
        connection = connect(self._database_path)
        try:
            with connection:
                connection.execute("INSERT INTO collector_status(key,value_json) VALUES('heartbeat',?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (json.dumps(payload),))
        finally: connection.close()
