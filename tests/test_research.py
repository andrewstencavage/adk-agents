from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from adk_agents.contracts import SpecialistTask, SpecialistType, TaskStatus
from adk_agents.research import (
    ResearchAgent,
    ResearchEvidence,
    ResearchEvidenceStore,
    ResearchRuntimeFailure,
    SearchResult,
)


def task() -> SpecialistTask:
    return SpecialistTask.model_validate(
        {
            "control_issue_ref": "#1",
            "story_ref": "#31",
            "dispatch_id": "dispatch-0001",
            "specialist": SpecialistType.RESEARCH.value,
            "objective": "Find one cited fact about local LLMs.",
            "acceptance_criteria": ["Return one cited finding."],
            "requested_by": "human:andrew",
            "deadline": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "budget_steps": 2,
            "research_model_fingerprint": {
                "runtime": "ollama",
                "model": "qwen2.5:7b",
                "model_artifact": "sha256:" + "a" * 64,
                "runtime_config": {"temperature": "0"},
            },
        }
    )


class FakeSearch:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.queries: list[str] = []

    def search(self, query: str):
        self.queries.append(query)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeEvidenceWriter:
    def __init__(self) -> None:
        self.events = []

    def write(self, event):
        self.events.append(event)
        return "sha256:" + f"{len(self.events):064x}"


def test_research_retries_one_transient_failure_then_completes_with_evidence():
    search = FakeSearch(
        [
            ResearchRuntimeFailure("runtime unavailable"),
            [SearchResult(title="Source", url="https://example.com", snippet="A cited fact.")],
        ]
    )
    evidence = FakeEvidenceWriter()

    result = ResearchAgent(search, evidence).run(task())

    assert result.status is TaskStatus.COMPLETED
    assert len(search.queries) == 2
    assert result.evidence_refs == ["sha256:" + f"{index:064x}" for index in range(1, 4)]
    assert [event.kind for event in evidence.events] == [
        "research.runtime_failure",
        "research.search",
        "research.completed",
    ]


def test_research_blocks_after_a_second_runtime_failure_without_fallback():
    search = FakeSearch([ResearchRuntimeFailure("runtime unavailable")] * 2)
    evidence = FakeEvidenceWriter()

    result = ResearchAgent(search, evidence).run(task())

    assert result.status is TaskStatus.BLOCKED
    assert result.next_manager_action == "block_story"
    assert len(search.queries) == 2
    assert result.evidence_refs == ["sha256:" + f"{index:064x}" for index in range(1, 4)]
    assert [event.kind for event in evidence.events] == [
        "research.runtime_failure",
        "research.runtime_failure",
        "research.blocked",
    ]


def test_research_exposes_only_typed_search_and_evidence_dependencies():
    agent = ResearchAgent(FakeSearch([[]]), FakeEvidenceWriter())

    assert not hasattr(agent, "shell")
    assert not hasattr(agent, "browser")
    assert not hasattr(agent, "github")
    assert not hasattr(agent, "credentials")


def test_evidence_store_appends_redacted_local_record_entries(tmp_path):
    store = ResearchEvidenceStore(tmp_path / "record.sqlite3")
    event = ResearchEvidence(
        dispatch_id="dispatch-0001",
        invocation_id="invocation-0001",
        kind="research.completed",
        objective_digest="sha256:" + "a" * 64,
        result_count=1,
    )

    reference = store.write(event)
    store.write(event)
    entries = store.entries()

    assert reference.startswith("sha256:")
    assert len(entries) == 2
    assert entries[0]["artifact_ref"] == reference
    assert entries[0]["invocation_id"] == "invocation-0001"
    assert entries[0]["input_digest"] == "sha256:" + "a" * 64
    assert b"Find one cited fact" not in (tmp_path / "record.sqlite3").read_bytes()
    assert store.artifact(reference)

    artifact_path = tmp_path / "research-artifacts" / f"{reference.removeprefix('sha256:')}.json"
    artifact_path.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="digest"):
        store.artifact(reference)
