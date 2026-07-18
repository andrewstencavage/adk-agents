"""Static Google ADK application topology; runtime routing supplies models at dispatch time."""

from __future__ import annotations

from google.adk.agents import Agent


def build_application() -> Agent:
    """Build the fixed Manager plus four least-privilege specialist tasks.

    Empty model identifiers deliberately keep local-model selection outside the
    topology; tests and deployment inject an eligible routed model later.
    """
    specialists = [
        Agent(name="scrum_master", description="Typed task-board operations only.", model="", mode="task", disallow_transfer_to_peers=True),
        Agent(name="research", description="Bounded cited web research only.", model="", mode="task", disallow_transfer_to_peers=True),
        Agent(name="coding", description="One isolated coding story only.", model="", mode="task", disallow_transfer_to_peers=True),
        Agent(name="review", description="Read-only review and PR handoff only.", model="", mode="task", disallow_transfer_to_peers=True),
    ]
    return Agent(name="manager", description="Root bounded task manager.", model="", mode="task", sub_agents=specialists, disallow_transfer_to_parent=True)
