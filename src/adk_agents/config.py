"""Explicit deployment configuration; secrets stay outside process arguments and source."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .task_board import BoardConfig


@dataclass(frozen=True)
class ServiceConfig:
    data_dir: Path
    backup_dir: Path
    github_project_id: str | None
    github_owner: str | None
    github_repository: str | None
    ready_option_id: str | None
    in_progress_option_id: str | None
    blocked_option_id: str | None
    github_status_field_id: str | None
    github_primary_specialist_field_id: str | None
    github_dispatch_field_id: str | None
    github_agent_summary_field_id: str | None
    github_project_token_env: str
    github_issues_token_env: str
    control_issue_number: int | None

    @classmethod
    def from_environment(cls) -> "ServiceConfig":
        data_dir = Path(os.environ.get("ADK_AGENTS_DATA_DIR", "/var/lib/adk-agents"))
        backup_dir = Path(os.environ.get("ADK_AGENTS_BACKUP_DIR", str(data_dir / "backups")))
        project_id = os.environ.get("ADK_AGENTS_GITHUB_PROJECT_ID")
        owner = os.environ.get("ADK_AGENTS_GITHUB_OWNER")
        repository = os.environ.get("ADK_AGENTS_GITHUB_REPOSITORY")
        ready = os.environ.get("ADK_AGENTS_READY_OPTION_ID")
        progress = os.environ.get("ADK_AGENTS_IN_PROGRESS_OPTION_ID")
        blocked = os.environ.get("ADK_AGENTS_BLOCKED_OPTION_ID")
        status_field = os.environ.get("ADK_AGENTS_GITHUB_STATUS_FIELD_ID")
        primary_field = os.environ.get("ADK_AGENTS_GITHUB_PRIMARY_SPECIALIST_FIELD_ID")
        dispatch_field = os.environ.get("ADK_AGENTS_GITHUB_DISPATCH_FIELD_ID")
        summary_field = os.environ.get("ADK_AGENTS_GITHUB_AGENT_SUMMARY_FIELD_ID")
        if any((owner, repository)) and not all((project_id, owner, repository)):
            raise ValueError("GitHub Project configuration requires project ID, owner, and repository together")
        if any((ready, progress, blocked)) and not all((project_id, owner, repository, ready, progress, blocked)):
            raise ValueError("GitHub board configuration requires all Project and status option IDs")
        if any((status_field, dispatch_field, primary_field, summary_field)) and not all((project_id, status_field, dispatch_field, primary_field, summary_field)):
            raise ValueError("GitHub Project writer configuration requires project, Status field, and Dispatch ID field IDs")
        project_token_env = os.environ.get("ADK_AGENTS_GITHUB_PROJECT_TOKEN_ENV", "GITHUB_TOKEN")
        issues_token_env = os.environ.get("ADK_AGENTS_GITHUB_ISSUES_TOKEN_ENV", "ADK_AGENTS_GITHUB_ISSUES_TOKEN")
        control_issue = os.environ.get("ADK_AGENTS_CONTROL_ISSUE_NUMBER")
        if not project_token_env.isidentifier() or not issues_token_env.isidentifier():
            raise ValueError("GitHub token configuration must name environment variables")
        if project_token_env == issues_token_env:
            raise ValueError("GitHub Project and Issues credentials must use separate environment variables")
        if control_issue is not None and (not control_issue.isdecimal() or int(control_issue) < 1):
            raise ValueError("Control issue number must be a positive integer")
        return cls(data_dir, backup_dir, project_id, owner, repository, ready, progress, blocked, status_field, primary_field, dispatch_field, summary_field, project_token_env, issues_token_env, None if control_issue is None else int(control_issue))

    def board_config(self) -> BoardConfig | None:
        if self.github_project_id is None:
            return None
        if self.ready_option_id is None and self.in_progress_option_id is None and self.blocked_option_id is None:
            return None
        if not all((self.github_owner, self.github_repository, self.ready_option_id, self.in_progress_option_id, self.blocked_option_id)):
            raise ValueError("GitHub Project status option IDs are required for dispatch")
        return BoardConfig(self.github_project_id, self.github_owner, self.github_repository, self.ready_option_id, self.in_progress_option_id, self.blocked_option_id)

    def project_writer_fields(self) -> tuple[str, str, str] | None:
        """Pinned field IDs required before the service may mutate a Project."""
        if self.github_status_field_id is None and self.github_dispatch_field_id is None and self.github_agent_summary_field_id is None:
            return None
        if self.github_status_field_id is None or self.github_dispatch_field_id is None or self.github_agent_summary_field_id is None:
            raise ValueError("GitHub Project writer requires both field IDs")
        return self.github_status_field_id, self.github_dispatch_field_id, self.github_agent_summary_field_id

    def project_reader_fields(self) -> tuple[str, str] | None:
        if self.github_status_field_id is None and self.github_primary_specialist_field_id is None:
            return None
        if self.github_status_field_id is None or self.github_primary_specialist_field_id is None:
            raise ValueError("GitHub Project reader requires pinned Status and Primary Specialist field IDs")
        return self.github_status_field_id, self.github_primary_specialist_field_id
