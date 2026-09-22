"""Independently runnable collector heartbeat loop."""
from __future__ import annotations

import os
import time
from pathlib import Path

from hsm.collector.service import Collector


def main() -> None:
    path = Path(os.environ.get("DATABASE_PATH", "./var/hsm.sqlite"))
    interval = 10
    collector = Collector(path)
    while True:
        collector.heartbeat()
        time.sleep(interval)


if __name__ == "__main__":
    main()
