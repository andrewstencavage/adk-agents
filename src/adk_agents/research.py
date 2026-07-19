"""Bounded Research execution with typed search and durable redacted evidence."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .contracts import SpecialistResult, SpecialistTask, TaskStatus

EvidenceKind = Literal[
    "research.search",
    "research.completed",
    "research.runtime_failure",
    "research.blocked",
]


class ResearchRuntimeFailure(RuntimeError):
    """A transient local runtime failure eligible for the single Research retry."""


class SearchResult(BaseModel):
    """One result returned by the typed DuckDuckGo search adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=1, max_length=2_000)
    snippet: str = Field(min_length=1, max_length=2_000)


class DuckDuckGoSearchAdapter(Protocol):
    """The only external capability granted to the MVP Research agent."""

    def search(self, query: str) -> Sequence[SearchResult]: ...


class ResearchEvidence(BaseModel):
    """Redacted, count-only event metadata retained by the local system record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dispatch_id: str = Field(min_length=1, max_length=128)
    invocation_id: str = Field(min_length=1, max_length=128)
    kind: EvidenceKind
    objective_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    result_count: int = Field(ge=0, le=100)
    error_class: str | None = Field(default=None, max_length=128)


class ImmutableEvidenceWriter(Protocol):
    """Writes one normalized immutable evidence event and returns its digest reference."""

    def write(self, event: ResearchEvidence) -> str: ...


class ResearchEvidenceStore:
    """Small SQLite-backed evidence writer for the MVP Research runtime."""

    _MIGRATION_VERSION = 1

    def __init__(self, database_path: str | Path) -> None:
        self._path = Path(database_path)
        self._artifact_directory = self._path.parent / "research-artifacts"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._artifact_directory.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def write(self, event: ResearchEvidence) -> str:
        """Append normalized evidence and retain its immutable manifest reference."""
        payload = event.model_dump(mode="json")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        evidence_ref = "sha256:" + hashlib.sha256(encoded).hexdigest()
        timestamp = datetime.now(timezone.utc).isoformat()
        artifact_path = self._artifact_directory / f"{evidence_ref.removeprefix('sha256:')}.json"
        if artifact_path.exists():
            _verify_artifact(artifact_path.read_bytes(), evidence_ref)
        else:
            artifact_path.write_bytes(encoded)
            _verify_artifact(encoded, evidence_ref)
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO research_artifact_manifest
                   (artifact_ref, payload_digest, artifact_name, created_at) VALUES (?, ?, ?, ?)""",
                (evidence_ref, evidence_ref, artifact_path.name, timestamp),
            )
            connection.execute(
                """INSERT INTO research_evidence_ledger
                   (event_id, dispatch_id, invocation_id, action_type, input_digest, output_digest, outcome, error_class, artifact_ref, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _uuid7(),
                    event.dispatch_id,
                    event.invocation_id,
                    event.kind,
                    event.objective_digest,
                    evidence_ref,
                    "blocked" if event.kind == "research.blocked" else "recorded",
                    event.error_class,
                    evidence_ref,
                    timestamp,
                ),
            )
        return evidence_ref

    def artifact(self, evidence_ref: str) -> bytes:
        """Read an immutable evidence artifact addressed by its manifest reference."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT artifact_name FROM research_artifact_manifest WHERE artifact_ref = ?",
                (evidence_ref,),
            ).fetchone()
        if row is None:
            raise ValueError("Research evidence artifact does not exist")
        payload = (self._artifact_directory / row["artifact_name"]).read_bytes()
        _verify_artifact(payload, evidence_ref)
        return payload

    def entries(self) -> list[dict[str, str | None]]:
        """Expose normalized ledger metadata for deterministic operational inspection."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT dispatch_id, invocation_id, action_type, input_digest, output_digest, outcome, error_class, artifact_ref "
                "FROM research_evidence_ledger ORDER BY created_at, event_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def _migrate(self) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS research_evidence_migration "
                    "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                exists = connection.execute(
                    "SELECT 1 FROM research_evidence_migration WHERE version = ?",
                    (self._MIGRATION_VERSION,),
                ).fetchone()
                if exists:
                    connection.commit()
                    return
                connection.execute(
                    """CREATE TABLE research_artifact_manifest (
                        artifact_ref TEXT PRIMARY KEY,
                        payload_digest TEXT NOT NULL,
                        artifact_name TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )"""
                )
                connection.execute(
                    """CREATE TABLE research_evidence_ledger (
                        event_id TEXT PRIMARY KEY,
                        dispatch_id TEXT NOT NULL,
                        invocation_id TEXT NOT NULL,
                        action_type TEXT NOT NULL,
                        input_digest TEXT NOT NULL,
                        output_digest TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        error_class TEXT,
                        artifact_ref TEXT NOT NULL REFERENCES research_artifact_manifest(artifact_ref),
                        created_at TEXT NOT NULL
                    )"""
                )
                connection.execute(
                    "INSERT INTO research_evidence_migration(version, applied_at) VALUES (?, ?)",
                    (self._MIGRATION_VERSION, datetime.now(timezone.utc).isoformat()),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection


class ResearchAgent:
    """Runs one admitted Research task with no provider or model fallback."""

    def __init__(self, search: DuckDuckGoSearchAdapter, evidence: ImmutableEvidenceWriter) -> None:
        self._search = search
        self._evidence = evidence

    def run(self, task: SpecialistTask) -> SpecialistResult:
        """Search once, retry one transient runtime failure, then complete or block."""
        objective_digest = _digest(task.objective)
        invocation_id = _uuid7()
        evidence_refs: list[str] = []
        for attempt in range(2):
            try:
                results = list(self._search.search(task.objective))
            except ResearchRuntimeFailure as error:
                evidence_refs.append(
                    self._write(task.dispatch_id, invocation_id, "research.runtime_failure", objective_digest, 0, type(error).__name__)
                )
                if attempt == 0:
                    continue
                evidence_refs.append(self._write(task.dispatch_id, invocation_id, "research.blocked", objective_digest, 0, type(error).__name__))
                return SpecialistResult(
                    status=TaskStatus.BLOCKED,
                    summary="Research runtime failed twice; the story requires user attention.",
                    next_manager_action="block_story",
                    evidence_refs=evidence_refs,
                )
            evidence_refs.append(self._write(task.dispatch_id, invocation_id, "research.search", objective_digest, len(results)))
            evidence_refs.append(self._write(task.dispatch_id, invocation_id, "research.completed", objective_digest, len(results)))
            return SpecialistResult(
                status=TaskStatus.COMPLETED,
                summary=f"Research returned {len(results)} typed search results.",
                next_manager_action="record_handoff",
                evidence_refs=evidence_refs,
            )
        raise AssertionError("Research retry loop must return a terminal result")

    def _write(
        self,
        dispatch_id: str,
        invocation_id: str,
        kind: EvidenceKind,
        objective_digest: str,
        result_count: int,
        error_class: str | None = None,
    ) -> str:
        return self._evidence.write(
            ResearchEvidence(
                dispatch_id=dispatch_id,
                invocation_id=invocation_id,
                kind=kind,
                objective_digest=objective_digest,
                result_count=result_count,
                error_class=error_class,
            )
        )


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _verify_artifact(payload: bytes, evidence_ref: str) -> None:
    if "sha256:" + hashlib.sha256(payload).hexdigest() != evidence_ref:
        raise ValueError("Research evidence artifact digest does not match its manifest reference")


def _uuid7() -> str:
    milliseconds = int(time.time() * 1_000)
    value = (milliseconds << 80) | (0x7 << 76) | (secrets.randbits(12) << 64)
    value |= (0b10 << 62) | secrets.randbits(62)
    return str(UUID(int=value))
