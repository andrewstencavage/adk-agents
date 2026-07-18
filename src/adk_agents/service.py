"""Minimal service entrypoint: verifies the local system record before work begins."""

from __future__ import annotations

from pathlib import Path

from .config import ServiceConfig
from .operational_record import OperationalRecord
from .manager import Manager, accepted_result
from .trace import TraceStore
from .service_loop import PollingService, task_from_issue_body


def build_mock_manager(data_dir: Path) -> Manager:
    """Explicit no-model Manager used only until capability assessments exist."""
    handlers = {"scrum_master": accepted_result, "research": accepted_result, "coding": accepted_result, "review": accepted_result}
    return Manager(TraceStore(data_dir / "record.sqlite3"), handlers)


def build_task_for(issue_body_reader):
    """Bind a read-only issue body reader to the poller's validated task seam."""
    return lambda story, dispatch_id: task_from_issue_body(issue_body_reader.body(story.issue_number), dispatch_id)


def build_polling_service(board, manager, workflow, project_reader, issue_body_reader) -> PollingService:
    """Compose the only dispatch path from Project Ready through durable handoff."""
    return PollingService(
        board, manager, workflow, project_reader.list_ready_stories, build_task_for(issue_body_reader)
    )


def main() -> None:
    """Run startup safety checks; a host scheduler owns the polling loop."""
    config = ServiceConfig.from_environment()
    config.backup_dir.mkdir(parents=True, exist_ok=True)
    OperationalRecord(config.data_dir / "record.sqlite3").startup()


if __name__ == "__main__":
    main()
