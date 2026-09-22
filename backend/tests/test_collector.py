from pathlib import Path
import json
import tempfile
import unittest

from hsm.collector import Collector, LatestSnapshot
from hsm.db import connect, migrate


class CollectorTests(unittest.TestCase):
    def test_heartbeat_is_persisted_without_lxd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "hsm.sqlite"
            Collector(database).heartbeat(now="2026-09-21T00:00:00+00:00")
            connection = connect(database)
            try:
                stored = connection.execute(
                    "SELECT value_json FROM collector_status WHERE key = ?", ("heartbeat",)
                ).fetchone()[0]
            finally:
                connection.close()
        self.assertEqual(json.loads(stored), {
            "observed_at": "2026-09-21T00:00:00+00:00", "state": "idle"
        })

    def test_latest_snapshot_upserts_only_known_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "hsm.sqlite"
            migrate(database)
            connection = connect(database)
            try:
                with connection:
                    connection.execute(
                        """INSERT INTO containers (
                            id, project, current_name, managed, isolation_status, lifecycle,
                            first_seen_at, last_seen_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        ("container-1", "hsm", "disposable", 1, "unprivileged", "present",
                         "2026-09-21T00:00:00+00:00", "2026-09-21T00:00:00+00:00"),
                    )
            finally:
                connection.close()
            collector = Collector(database)
            collector.record_latest(LatestSnapshot(
                container_id="container-1", state="Running", ipv4=("10.70.0.2",),
                sampled_at="2026-09-21T00:00:10+00:00", cpu_pct=12.5,
            ))
            collector.record_latest(LatestSnapshot(
                container_id="container-1", state="Stopped", ipv4=(),
                sampled_at="2026-09-21T00:00:20+00:00",
            ))
            connection = connect(database)
            try:
                stored = connection.execute(
                    "SELECT state, ipv4_json, sampled_at FROM metrics_latest WHERE container_id = ?",
                    ("container-1",),
                ).fetchone()
            finally:
                connection.close()
        self.assertEqual(stored, ("Stopped", "[]", "2026-09-21T00:00:20+00:00"))
