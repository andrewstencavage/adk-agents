from adk_agents.service import build_mock_manager


def test_mock_manager_has_only_the_static_specialist_registry(tmp_path):
    manager = build_mock_manager(tmp_path)
    assert set(manager._specialists) == {"scrum_master", "research", "coding", "review"}
