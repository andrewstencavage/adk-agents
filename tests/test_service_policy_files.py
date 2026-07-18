from pathlib import Path


def test_systemd_service_uses_dedicated_identity_and_bounded_restart_policy():
    unit = Path("deploy/systemd/adk-agents.service").read_text()

    assert "User=adk-agents" in unit
    assert "RestartSec=10" in unit
    assert "[Unit]\nDescription=ADK agents local service\nStartLimitIntervalSec=600\nStartLimitBurst=3" in unit
    assert "-m adk_agents.service" in unit


def test_journal_policy_has_the_specified_time_and_size_bounds():
    policy = Path("deploy/systemd/journald-adk-agents.conf").read_text()

    assert "MaxRetentionSec=30day" in policy
    assert "SystemMaxUse=512M" in policy
