"""Synthetic TinyFlux partition feasibility probe; not an application collector.

Run each container-count case in a fresh Linux process. This process alone owns
its temporary database; no API or other reader shares the file.
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import resource
import tempfile
from time import perf_counter

from tinyflux import Point, TagQuery, TinyFlux


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--containers", type=int, choices=(1, 10), default=1)
    parser.add_argument("--hours", type=int, choices=(1, 24), default=24)
    args = parser.parse_args()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ticks = 360 * args.hours
    count = ticks * args.containers
    with tempfile.TemporaryDirectory(prefix="hsm-tinyflux-") as directory:
        path = Path(directory) / "container_metrics-2026-01-01.csv"
        started = perf_counter()
        with TinyFlux(str(path)) as database:
            # One simulated hour at a time avoids constructing a month in RAM.
            for hour in range(args.hours):
                points = []
                for tick in range(hour * 360, (hour + 1) * 360):
                    for container in range(args.containers):
                        points.append(Point(
                            time=start + timedelta(seconds=tick * 10),
                            measurement="container_metrics",
                            tags={"id": f"00000000-0000-4000-8000-{container:012d}",
                                  "project": "hsm", "name": f"probe-{container}"},
                            fields={"cpu_ns": tick * 100000000,
                                    "cpu_core_equivalent": 0.01, "cpu_pct": 1.0,
                                    "ram_used_bytes": 134217728,
                                    "ram_limit_bytes": 536870912,
                                    "disk_used_bytes": 536870912,
                                    "disk_limit_bytes": 2147483648,
                                    "net_rx_bytes": tick * 4096,
                                    "net_tx_bytes": tick * 2048,
                                    "processes": 12, "uptime_s": tick * 10,
                                    "state_code": 1},
                        ))
                database.insert_multiple(points)
        write_seconds = perf_counter() - started
        started = perf_counter()
        with TinyFlux(str(path)) as database:
            result = database.search(
                TagQuery().id == "00000000-0000-4000-8000-000000000000"
            )
            assert len(database) == count
            assert len(result) == ticks
            assert result[-1].fields["uptime_s"] == (ticks - 1) * 10
        read_seconds = perf_counter() - started
        size = path.stat().st_size
        print(json.dumps({
            "synthetic": True, "containers": args.containers, "hours": args.hours,
            "samples": count, "partition_bytes": size,
            "bytes_per_sample": size / count,
            "write_seconds": write_seconds,
            "reopen_query_validate_seconds": read_seconds,
            "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "projected_30_day_bytes_without_retention": size * 24 / args.hours * 30,
            "persisted_points_verified": True,
        }, indent=2))


if __name__ == "__main__":
    main()
