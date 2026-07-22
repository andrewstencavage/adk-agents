"""Durable SQLite records for Story intake and continuation recovery."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class StoredIntake:
    comment_id: str
    source_digest: str
    source_request: str
    intake_id: str
    state: str
    reply_id: str | None
    assessment_json: str | None
    publication_conflict_status: str | None
    publication_complete: bool
    issue_create_attempted: bool
    published_story_number: int | None
    published_story_url: str | None
    published_project_item_id: str | None


@dataclass(frozen=True)
class StoredContinuation:
    comment_id: str
    intake_id: str
    answer: str
    reply_id: str | None


@dataclass(frozen=True)
class StoredStory:
    number: int
    url: str
    project_item_id: str


class StoryIntakeStore:
    """Owns Story intake schema migration and all durable recovery records."""

    _MIGRATION_VERSION = 1

    def __init__(self, database_path: str | Path) -> None:
        self._path = Path(database_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def begin(self, *, comment_id: str, source_digest: str, source_request: str, intake_id: str, state: str) -> StoredIntake:
        existing = self.intake_for_comment(comment_id)
        if existing is not None:
            return existing
        with self._connect() as database:
            database.execute(
                "INSERT INTO story_intake(comment_id, source_digest, source_request, intake_id, state) VALUES (?, ?, ?, ?, ?)",
                (comment_id, source_digest, source_request, intake_id, state),
            )
        return self.intake_for_comment(comment_id) or self._missing_intake(comment_id)

    def intake_for_comment(self, comment_id: str) -> StoredIntake | None:
        with self._connect() as database:
            row = database.execute("SELECT * FROM story_intake WHERE comment_id = ?", (comment_id,)).fetchone()
        return None if row is None else self._intake_from_row(row)

    def intake(self, intake_id: str) -> StoredIntake | None:
        with self._connect() as database:
            row = database.execute("SELECT * FROM story_intake WHERE intake_id = ?", (intake_id,)).fetchone()
        return None if row is None else self._intake_from_row(row)

    def record_reply(self, comment_id: str, state: str, reply_id: str) -> None:
        self._update("UPDATE story_intake SET state = ?, reply_id = ? WHERE comment_id = ?", (state, reply_id, comment_id))

    def record_assessment(self, intake_id: str, state: str, assessment_json: str) -> None:
        self._update("UPDATE story_intake SET state = ?, assessment_json = ? WHERE intake_id = ?", (state, assessment_json, intake_id))

    def continuation_for_comment(self, comment_id: str) -> StoredContinuation | None:
        with self._connect() as database:
            row = database.execute("SELECT * FROM story_intake_continuation WHERE comment_id = ?", (comment_id,)).fetchone()
        return None if row is None else StoredContinuation(**dict(row))

    def record_continuation(self, comment_id: str, intake_id: str, answer: str) -> None:
        with self._connect() as database:
            database.execute(
                "INSERT INTO story_intake_continuation(comment_id, intake_id, answer) VALUES (?, ?, ?)",
                (comment_id, intake_id, answer),
            )

    def continuation_answers(self, intake_id: str) -> tuple[str, ...]:
        with self._connect() as database:
            rows = database.execute(
                "SELECT answer FROM story_intake_continuation WHERE intake_id = ? ORDER BY rowid", (intake_id,)
            )
            return tuple(row["answer"] for row in rows)

    def record_continuation_reply(self, comment_id: str, reply_id: str) -> None:
        self._update("UPDATE story_intake_continuation SET reply_id = ? WHERE comment_id = ?", (reply_id, comment_id))

    def record_conflict(self, intake_id: str, status: str) -> None:
        self._update("UPDATE story_intake SET publication_conflict_status = ? WHERE intake_id = ?", (status, intake_id))

    def record_issue_create_attempt(self, intake_id: str) -> None:
        self._update("UPDATE story_intake SET issue_create_attempted = 1 WHERE intake_id = ?", (intake_id,))

    def record_story(self, intake_id: str, story: StoredStory) -> None:
        self._update(
            "UPDATE story_intake SET published_story_number = ?, published_story_url = ?, published_project_item_id = ? WHERE intake_id = ?",
            (story.number, story.url, story.project_item_id, intake_id),
        )

    def record_published(self, intake_id: str, story: StoredStory) -> None:
        self.record_story(intake_id, story)
        self._update("UPDATE story_intake SET publication_complete = 1 WHERE intake_id = ?", (intake_id,))

    def _update(self, statement: str, values: tuple[object, ...]) -> None:
        with self._connect() as database:
            database.execute(statement, values)

    def _migrate(self) -> None:
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            try:
                database.execute("CREATE TABLE IF NOT EXISTS story_intake_migration (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
                migrated = database.execute("SELECT 1 FROM story_intake_migration WHERE version = ?", (self._MIGRATION_VERSION,)).fetchone()
                if migrated is None:
                    database.executescript("""
                        CREATE TABLE IF NOT EXISTS story_intake (
                            comment_id TEXT PRIMARY KEY, source_digest TEXT NOT NULL, source_request TEXT NOT NULL DEFAULT '',
                            intake_id TEXT NOT NULL, state TEXT NOT NULL, reply_id TEXT, assessment_json TEXT,
                            published_story_number INTEGER, published_story_url TEXT, published_project_item_id TEXT,
                            publication_complete INTEGER NOT NULL DEFAULT 0, issue_create_attempted INTEGER NOT NULL DEFAULT 0,
                            publication_conflict_status TEXT
                        );
                        CREATE TABLE IF NOT EXISTS story_intake_continuation (
                            comment_id TEXT PRIMARY KEY, intake_id TEXT NOT NULL, answer TEXT NOT NULL, reply_id TEXT
                        );
                    """)
                    self._add_columns(database)
                    database.execute("INSERT INTO story_intake_migration(version, applied_at) VALUES (?, ?)", (self._MIGRATION_VERSION, datetime.now(timezone.utc).isoformat()))
                database.commit()
            except Exception:
                database.rollback()
                raise

    @staticmethod
    def _add_columns(database: sqlite3.Connection) -> None:
        definitions = {
            "story_intake": ("source_request TEXT NOT NULL DEFAULT ''", "assessment_json TEXT", "published_story_number INTEGER", "published_story_url TEXT", "published_project_item_id TEXT", "publication_complete INTEGER NOT NULL DEFAULT 0", "issue_create_attempted INTEGER NOT NULL DEFAULT 0", "publication_conflict_status TEXT"),
            "story_intake_continuation": ("reply_id TEXT",),
        }
        for table, columns in definitions.items():
            existing = {row["name"] for row in database.execute(f"PRAGMA table_info({table})")}
            for definition in columns:
                if (name := definition.split()[0]) not in existing:
                    database.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")

    @staticmethod
    def _intake_from_row(row: sqlite3.Row) -> StoredIntake:
        return StoredIntake(
            comment_id=row["comment_id"], source_digest=row["source_digest"], source_request=row["source_request"],
            intake_id=row["intake_id"], state=row["state"], reply_id=row["reply_id"], assessment_json=row["assessment_json"],
            publication_conflict_status=row["publication_conflict_status"], publication_complete=bool(row["publication_complete"]),
            issue_create_attempted=bool(row["issue_create_attempted"]), published_story_number=row["published_story_number"],
            published_story_url=row["published_story_url"], published_project_item_id=row["published_project_item_id"],
        )

    @staticmethod
    def _missing_intake(comment_id: str) -> StoredIntake:
        raise RuntimeError(f"Story intake record was not saved for {comment_id}")

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self._path)
        database.row_factory = sqlite3.Row
        return database
