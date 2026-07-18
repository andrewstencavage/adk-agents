"""Explicit deployment configuration; secrets stay outside process arguments and source."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServiceConfig:
    data_dir: Path
    backup_dir: Path
    github_project_id: str | None
    github_owner: str | None
    github_repository: str | None

    @classmethod
    def from_environment(cls) -> "ServiceConfig":
        data_dir = Path(os.environ.get("ADK_AGENTS_DATA_DIR", "/var/lib/adk-agents"))
        backup_dir = Path(os.environ.get("ADK_AGENTS_BACKUP_DIR", str(data_dir / "backups")))
        project_id = os.environ.get("ADK_AGENTS_GITHUB_PROJECT_ID")
        owner = os.environ.get("ADK_AGENTS_GITHUB_OWNER")
        repository = os.environ.get("ADK_AGENTS_GITHUB_REPOSITORY")
        if any((owner, repository)) and not all((project_id, owner, repository)):
            raise ValueError("GitHub Project configuration requires project ID, owner, and repository together")
        return cls(data_dir, backup_dir, project_id, owner, repository)
