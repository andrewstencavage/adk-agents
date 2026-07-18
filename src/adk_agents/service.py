"""Minimal service entrypoint: verifies the local system record before work begins."""

from __future__ import annotations

import os
from pathlib import Path

from .operational_record import OperationalRecord


def main() -> None:
    """Run startup safety checks; a host scheduler owns the polling loop."""
    data_dir = Path(os.environ.get("ADK_AGENTS_DATA_DIR", "/var/lib/adk-agents"))
    OperationalRecord(data_dir / "record.sqlite3").startup()


if __name__ == "__main__":
    main()
