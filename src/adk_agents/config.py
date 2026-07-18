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
    github_token_env: str

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
        if any((owner, repository)) and not all((project_id, owner, repository)):
            raise ValueError("GitHub Project configuration requires project ID, owner, and repository together")
        if any((ready, progress, blocked)) and not all((project_id, owner, repository, ready, progress, blocked)):
            raise ValueError("GitHub board configuration requires all Project and status option IDs")
        token_env = os.environ.get("ADK_AGENTS_GITHUB_TOKEN_ENV", "GITHUB_TOKEN")
        if not token_env.isidentifier():
            raise ValueError("GitHub token configuration must name an environment variable")
        return cls(data_dir, backup_dir, project_id, owner, repository, ready, progress, blocked, token_env)

    def board_config(self) -> BoardConfig | None:
        if self.github_project_id is None:
            return None
        if not all((self.github_owner, self.github_repository, self.ready_option_id, self.in_progress_option_id, self.blocked_option_id)):
            raise ValueError("GitHub Project status option IDs are required for dispatch")
        return BoardConfig(self.github_project_id, self.github_owner, self.github_repository, self.ready_option_id, self.in_progress_option_id, self.blocked_option_id)
