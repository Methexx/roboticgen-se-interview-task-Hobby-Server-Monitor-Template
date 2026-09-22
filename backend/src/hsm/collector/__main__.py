"""Independently runnable collector heartbeat loop."""
from __future__ import annotations

import os
import time
import signal
import threading
from pathlib import Path

from hsm.collector.service import Collector
from pylxd import Client
from hsm.db import migrate


def main() -> None:
    path = Path(os.environ.get("DATABASE_PATH", "./var/hsm.sqlite"))
    migrate(path)
    collector = Collector(path, Path(os.environ.get("TINYFLUX_DIR", str(path.parent / "metrics"))),
                          int(os.environ.get("METRICS_RETENTION_DAYS", "7")) * 24,
                          int(os.environ.get("METRICS_MAX_BYTES", "268435456")))
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    while not stop.is_set():
        began = time.monotonic()
        try:
            collector.poll_lxd(Client, int(os.environ.get("LXD_TIMEOUT_SECONDS", "10")))
        except Exception as error:
            collector._status({"state": "collector_error", "error": type(error).__name__})
        stop.wait(max(0, 10 - (time.monotonic() - began)))


if __name__ == "__main__":
    main()
