from adk_agents.adk_application import build_application


def test_adk_application_exposes_only_the_manager_and_four_named_specialists_without_a_local_model():
    manager = build_application()

    assert manager.name == "manager"
    assert {agent.name for agent in manager.sub_agents} == {"scrum_master", "research", "coding", "review"}
    assert all(agent.model == "" for agent in manager.sub_agents)
