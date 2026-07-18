"""Minimal service entrypoint: verifies the local system record before work begins."""

from __future__ import annotations

from .config import ServiceConfig
from .operational_record import OperationalRecord


def main() -> None:
    """Run startup safety checks; a host scheduler owns the polling loop."""
    config = ServiceConfig.from_environment()
    config.backup_dir.mkdir(parents=True, exist_ok=True)
    OperationalRecord(config.data_dir / "record.sqlite3").startup()


if __name__ == "__main__":
    main()
