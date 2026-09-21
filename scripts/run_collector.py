"""Run one non-invasive collector heartbeat for service wiring checks."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend" / "src"))

from hsm.collector import Collector  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="./var/hsm.sqlite")
    args = parser.parse_args()
    Collector(Path(args.database)).heartbeat()


if __name__ == "__main__":
    main()
