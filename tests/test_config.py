from pathlib import Path

from adk_agents.config import ServiceConfig
from adk_agents.task_board import BoardConfig


def test_service_configuration_defaults_backups_to_a_same_drive_child_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("ADK_AGENTS_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("ADK_AGENTS_BACKUP_DIR", raising=False)

    config = ServiceConfig.from_environment()

    assert config.data_dir == tmp_path / "state"
    assert config.backup_dir == tmp_path / "state" / "backups"
    assert config.github_project_id is None


def test_service_configuration_accepts_explicit_github_and_backup_values(monkeypatch, tmp_path):
    monkeypatch.setenv("ADK_AGENTS_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ADK_AGENTS_BACKUP_DIR", str(tmp_path / "backup"))
    monkeypatch.setenv("ADK_AGENTS_GITHUB_PROJECT_ID", "PVT_1")
    monkeypatch.setenv("ADK_AGENTS_GITHUB_OWNER", "owner")
    monkeypatch.setenv("ADK_AGENTS_GITHUB_REPOSITORY", "repo")

    config = ServiceConfig.from_environment()

    assert config.backup_dir == tmp_path / "backup"
    assert (config.github_project_id, config.github_owner, config.github_repository) == ("PVT_1", "owner", "repo")
    assert config.github_token_env == "GITHUB_TOKEN"


def test_project_configuration_is_constructed_only_when_all_protocol_ids_are_present(monkeypatch, tmp_path):
    monkeypatch.setenv("ADK_AGENTS_DATA_DIR", str(tmp_path / "state"))
    for name, value in {
        "ADK_AGENTS_GITHUB_PROJECT_ID": "PVT_1",
        "ADK_AGENTS_GITHUB_OWNER": "owner",
        "ADK_AGENTS_GITHUB_REPOSITORY": "repo",
        "ADK_AGENTS_READY_OPTION_ID": "ready",
        "ADK_AGENTS_IN_PROGRESS_OPTION_ID": "progress",
        "ADK_AGENTS_BLOCKED_OPTION_ID": "blocked",
    }.items():
        monkeypatch.setenv(name, value)

    config = ServiceConfig.from_environment()

    assert config.board_config() == BoardConfig("PVT_1", "owner", "repo", "ready", "progress", "blocked")
