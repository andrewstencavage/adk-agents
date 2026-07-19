"""Minimal service entrypoint: verifies the local system record before work begins."""

from __future__ import annotations

import os
from pathlib import Path

from .config import ServiceConfig
from .operational_record import OperationalRecord
from .manager import Manager, accepted_result
from .contracts import SpecialistType
from .routing import ModelRouter
from .trace import TraceStore
from .service_loop import LeasedPollingWorker, PollingService, task_from_issue_body
from .task_board import DispatchStore, TaskBoardAdapter
from .github_project_reader import GitHubGraphQLTransport, GitHubIssueBodyReader, GitHubIssueComments, GitHubProjectFieldWriter, GitHubProjectReader, GitHubTaskBoardGateway, resolve_status_option_ids
from .integration import ApprovedStoryWorkflow
from .evidence import EvidenceLedger
from .ids import uuid7
from .operational_record import PollingLease


def build_mock_manager(data_dir: Path) -> Manager:
    """Explicit no-model Manager used only until capability assessments exist."""
    handlers = {"scrum_master": accepted_result, "research": accepted_result, "coding": accepted_result, "review": accepted_result}
    return Manager(TraceStore(data_dir / "record.sqlite3"), handlers)


def build_assessment_gated_manager(record: OperationalRecord) -> Manager:
    """Create the static specialist registry behind an empty assessed inventory.

    No local model becomes eligible merely because the service is installed.
    The ModelRouter records a blocked decision until a capability assessment has
    supplied a current exact-fingerprint candidate.
    """
    handlers = {role.value: accepted_result for role in SpecialistType}
    return Manager(TraceStore(record.path), handlers, router=ModelRouter(record, suite_version="2026.1"), inventory=lambda: [])


def build_task_for(issue_body_reader):
    """Bind a read-only issue body reader to the poller's validated task seam."""
    return lambda story, dispatch_id: task_from_issue_body(issue_body_reader.body(story.issue_number), dispatch_id)


def build_polling_service(board, manager, workflow, project_reader, issue_body_reader) -> PollingService:
    """Compose the only dispatch path from Project Ready through durable handoff."""
    return PollingService(
        board, manager, workflow, project_reader.list_ready_stories, build_task_for(issue_body_reader)
    )


def build_live_polling_worker(config: ServiceConfig, record: OperationalRecord) -> LeasedPollingWorker:
    """Compose the constrained GitHub board and assessment-gated dispatch path.

    This is selected only by explicit ``ADK_AGENTS_SERVICE_MODE=live``. With no
    passing capability assessment it claims a Ready story, records a redacted
    handoff, and moves that matching story to Blocked rather than executing a
    specialist handler.
    """
    writer_fields = config.project_writer_fields()
    reader_fields = config.project_reader_fields()
    if writer_fields is None or reader_fields is None:
        raise ValueError("live polling requires complete GitHub Project configuration")
    project_token = os.environ.get(config.github_project_token_env)
    issues_token = os.environ.get(config.github_issues_token_env)
    if not project_token or not issues_token:
        raise ValueError("live polling requires configured Project and Issues credentials")
    status_field_id, dispatch_field_id, agent_summary_field_id = writer_fields
    _reader_status_field_id, primary_specialist_field_id = reader_fields
    project_graphql = GitHubGraphQLTransport(project_token)
    board_config = config.board_config()
    if board_config is None:
        if not all((config.github_project_id, config.github_owner, config.github_repository)):
            raise ValueError("live polling requires complete GitHub Project configuration")
        ready_option_id, in_progress_option_id, blocked_option_id = resolve_status_option_ids(
            project_graphql, config.github_project_id, status_field_id
        )
        board_config = BoardConfig(
            config.github_project_id,
            config.github_owner,
            config.github_repository,
            ready_option_id,
            in_progress_option_id,
            blocked_option_id,
        )
    reader = GitHubProjectReader(board_config, project_graphql, status_field_id=status_field_id, primary_specialist_field_id=primary_specialist_field_id, dispatch_field_id=dispatch_field_id)
    writer = GitHubProjectFieldWriter(
        project_graphql, project_id=board_config.project_id, status_field_id=status_field_id,
        dispatch_field_id=dispatch_field_id, agent_summary_field_id=agent_summary_field_id, in_progress_option_id=board_config.in_progress_option_id,
        blocked_option_id=board_config.blocked_option_id,
    )
    gateway = GitHubTaskBoardGateway(reader, writer, GitHubIssueComments(issues_token, board_config.owner, board_config.repository))
    board = TaskBoardAdapter(board_config, gateway, DispatchStore(record.path))
    workflow = ApprovedStoryWorkflow(EvidenceLedger(record), lambda _event: None)
    polling = build_polling_service(
        board, build_assessment_gated_manager(record), workflow, reader,
        GitHubIssueBodyReader(issues_token, board_config.owner, board_config.repository),
    )
    return LeasedPollingWorker(polling, PollingLease(record, project_id=board_config.project_id, owner_id=uuid7()))


def run_mock_polling_loop() -> None:
    """Keep the supervised service alive without touching GitHub or any model."""
    class Noop:
        def __getattr__(self, _name):
            raise AssertionError("a mock polling tick must not reach an external boundary")

    PollingService(Noop(), Noop(), Noop(), lambda: (), lambda *_: {}).run_forever(interval_seconds=60)


def run_live_polling_loop(config: ServiceConfig, record: OperationalRecord) -> None:
    build_live_polling_worker(config, record).run_forever(interval_seconds=60)


def main() -> None:
    """Run startup checks and, only when explicit, a safe no-op supervised poller."""
    config = ServiceConfig.from_environment()
    config.backup_dir.mkdir(parents=True, exist_ok=True)
    record = OperationalRecord(config.data_dir / "record.sqlite3")
    record.startup()
    mode = os.environ.get("ADK_AGENTS_SERVICE_MODE")
    if mode == "mock":
        run_mock_polling_loop()
    elif mode == "live":
        run_live_polling_loop(config, record)
    elif mode not in (None, ""):
        raise ValueError("ADK_AGENTS_SERVICE_MODE must be mock or live")


if __name__ == "__main__":
    main()
