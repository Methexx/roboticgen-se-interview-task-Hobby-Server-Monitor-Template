"""Independent LXD snapshots, bounded chart cache, and hourly TinyFlux samples."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
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

    def __init__(self, database_path: Path, metrics_dir: Path | None = None, retention_hours: int = 168, max_bytes: int = 268435456, cache_rows: int = 100000) -> None:
        self._database_path = database_path
        self._metrics_dir = metrics_dir or database_path.parent / "metrics"
        self._retention_hours = retention_hours
        self._max_bytes = max_bytes
        self._cache_rows = cache_rows
        self._previous: dict[str, tuple[float, int | None, int | None, int | None]] = {}

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
                cutoff = (datetime.now(timezone.utc) - timedelta(hours=self._retention_hours)).isoformat()
                connection.execute("DELETE FROM metrics_history WHERE sampled_at < ?", (cutoff,))
                connection.execute("DELETE FROM metrics_history WHERE rowid IN (SELECT rowid FROM metrics_history ORDER BY sampled_at DESC LIMIT -1 OFFSET ?)", (self._cache_rows,))
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
            tags = {"container_id": snapshot.container_id, "state": snapshot.state,
                    "ipv4_json": json.dumps(snapshot.ipv4)}
            tags.update({k: v for k, v in {"image": snapshot.image, "os_version": snapshot.os_version}.items() if v is not None})
            with TinyFlux(str(path)) as database:
                database.insert(Point(measurement="container", time=stamp, tags=tags, fields=fields))
            self._retain()
            self._status({"observed_at": _utc_now(), "state": "ok"}, key="history_store")
        except Exception as error:
            self._status({"observed_at": _utc_now(), "state": "tinyflux_error", "error": type(error).__name__}, key="history_store")

    def _retain(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self._retention_hours)
        for path in sorted(self._metrics_dir.glob("metrics-*.tinyflux")):
            partition = datetime.strptime(path.stem, "metrics-%Y%m%d%H").replace(tzinfo=timezone.utc)
            if partition + timedelta(hours=1) <= cutoff:
                path.unlink()
        paths = sorted(self._metrics_dir.glob("metrics-*.tinyflux"))
        total = sum(path.stat().st_size for path in paths)
        for path in paths:
            if total <= self._max_bytes:
                break
            total -= path.stat().st_size
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
                    state = dict(instance.state())
                    self.record_latest(self._snapshot(row[0], instance, state, now))
            except Exception:
                failures.append(project)
        self._status({"observed_at": now, "state": "partial" if failures else "ok", "project_errors": failures, "last_successful_collection": now if not failures else None})

    @staticmethod
    def _number(value: object) -> int | None:
        return value if type(value) is int and value >= 0 else None

    def _snapshot(self, identifier: str, instance: Any, state: dict, now: str) -> LatestSnapshot:
        network = state.get("network") or {}
        interfaces = [v for k, v in network.items() if k != "lo" and isinstance(v, dict)]
        def total(counter: str) -> int | None:
            values = [self._number(v.get("counters", {}).get(counter)) for v in interfaces]
            return sum(values) if values and all(v is not None for v in values) else None
        rx, tx = total("bytes_received"), total("bytes_sent")
        cpu = self._number((state.get("cpu") or {}).get("usage"))
        current = time.monotonic()
        previous = self._previous.get(identifier)
        self._previous[identifier] = (current, cpu, rx, tx)
        def rate(value: int | None, index: int) -> float | None:
            if previous is None or value is None or previous[index] is None or value < previous[index] or current <= previous[0]:
                return None
            return (value - previous[index]) / (current - previous[0])
        core_rate = rate(cpu, 1)
        cores = None if core_rate is None else core_rate / 1e9
        disks = list((state.get("disk") or {}).values())
        used = [self._number(v.get("usage")) for v in disks if isinstance(v, dict)]
        disk = sum(used) if used and all(v is not None for v in used) else None
        config = getattr(instance, "expanded_config", None) or instance.config
        ipv4 = tuple(a["address"] for v in interfaces for a in v.get("addresses", [])
                     if a.get("family") == "inet" and a.get("scope") == "global" and a.get("address"))
        uptime = None
        try:
            started = datetime.fromisoformat(str(instance.last_used_at).replace("Z", "+00:00"))
            if state.get("status") == "Running" and started.year > 1970:
                uptime = max(0, int((datetime.fromisoformat(now) - started).total_seconds()))
        except (AttributeError, ValueError, TypeError):
            pass
        return LatestSnapshot(identifier, str(state.get("status", "unknown")), ipv4, now,
            image=config.get("image.description"), os_version=config.get("image.release"),
            uptime_s=uptime, processes=self._number(state.get("processes")),
            cpu_pct=None if cores is None else cores * 100, cpu_core_equivalent=cores,
            ram_used_bytes=self._number((state.get("memory") or {}).get("usage")),
            disk_used_bytes=disk, net_rx_bytes=rx, net_tx_bytes=tx,
            net_rx_bps=rate(rx, 2), net_tx_bps=rate(tx, 3))

    def _status(self, payload: dict[str, object], *, key: str = "heartbeat") -> None:
        connection = connect(self._database_path)
        try:
            with connection:
                previous = connection.execute("SELECT value_json FROM collector_status WHERE key=?", (key,)).fetchone()
                if previous and not payload.get("last_successful_collection"):
                    last = json.loads(previous[0]).get("last_successful_collection")
                    if last:
                        payload["last_successful_collection"] = last
                connection.execute("INSERT INTO collector_status(key,value_json) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (key, json.dumps(payload)))
        finally: connection.close()
