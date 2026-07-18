"""Immutable, redacted evidence storage and append-only ledger records."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .operational_record import OperationalRecord


def _digest(value: Any) -> str:
    if isinstance(value, bytes):
        encoded = value
    else:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True)
class ArtifactManifest:
    digest: str
    logical_type: str
    byte_size: int
    storage_path: Path


class ArtifactStore:
    """Writes only content-addressed evidence files outside story worktrees."""

    def __init__(self, record: OperationalRecord, directory: str | Path) -> None:
        self._record = record
        self._directory = Path(directory)

    def write(self, payload: bytes, *, logical_type: str, digest: str | None = None, retention_class: str = "routine") -> ArtifactManifest:
        actual_digest = _digest(payload)
        if digest is not None and digest != actual_digest:
            raise FileExistsError("provided digest does not match artifact bytes")
        target = self._directory / actual_digest.removeprefix("sha256:")[:2] / actual_digest.removeprefix("sha256:")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if target.read_bytes() != payload:
                raise
        else:
            with os.fdopen(descriptor, "wb") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            target.chmod(0o400)
        manifest = ArtifactManifest(actual_digest, logical_type, len(payload), target)
        with self._record.connection() as connection:
            existing = connection.execute("SELECT logical_type, retention_class FROM artifact_manifest WHERE digest = ?", (manifest.digest,)).fetchone()
            if existing is not None and (existing["logical_type"], existing["retention_class"]) != (logical_type, retention_class):
                raise ValueError("existing artifact metadata conflicts with its digest")
            connection.execute(
                "INSERT OR IGNORE INTO artifact_manifest (digest, logical_type, byte_size, storage_path, retention_class, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (manifest.digest, logical_type, manifest.byte_size, str(target), retention_class, _now()),
            )
        return manifest

    def read(self, digest: str) -> bytes:
        payload = (self._directory / digest.removeprefix("sha256:")[:2] / digest.removeprefix("sha256:")).read_bytes()
        if _digest(payload) != digest:
            raise ValueError("artifact bytes do not match their content address")
        return payload


class EvidenceLedger:
    """Records event classifications and SHA-256 digests, never raw values."""

    def __init__(self, record: OperationalRecord) -> None:
        self._record = record

    @property
    def record(self) -> OperationalRecord:
        """The shared durable record used by the owning workflow."""
        return self._record

    def append(self, *, action_type: str, input_value: Any = None, output_value: Any = None, dispatch_id: str | None = None, invocation_id: str | None = None, outcome_class: str | None = None, error_class: str | None = None, artifact_digest: str | None = None) -> str:
        event_id = str(uuid4())
        with self._record.connection() as connection:
            connection.execute(
                "INSERT INTO evidence_ledger (event_id, dispatch_id, invocation_id, action_type, input_digest, output_digest, outcome_class, error_class, artifact_digest, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, dispatch_id, invocation_id, action_type, _digest(input_value) if input_value is not None else None, _digest(output_value) if output_value is not None else None, outcome_class, error_class, artifact_digest, _now()),
            )
        return event_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
