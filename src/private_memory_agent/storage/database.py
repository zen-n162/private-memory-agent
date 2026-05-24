"""SQLite connection and migration helpers."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from private_memory_agent.storage.migrations import MIGRATIONS, Migration
from private_memory_agent.storage.repositories import (
    AuditLogRepository,
    EmbeddingRepository,
    EntityRepository,
    EventRepository,
    EvidenceLinkRepository,
    LineMessageRepository,
    MediaAnnotationRepository,
    MediaItemRepository,
    NoteRepository,
    SourceItemRepository,
    TextAnnotationRepository,
)


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a SQLite connection with local metadata defaults."""

    if str(db_path) != ":memory:":
        Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(db_path: Path | str) -> "Storage":
    """Open a database, apply migrations, and return a storage wrapper."""

    connection = connect(db_path)
    apply_migrations(connection)
    return Storage(connection)


def apply_migrations(connection: sqlite3.Connection) -> list[int]:
    """Apply pending migrations and return applied versions from this call."""

    _ensure_migration_table(connection)
    applied_versions = _applied_versions(connection)
    applied_now: list[int] = []
    for migration in MIGRATIONS:
        if migration.version in applied_versions:
            continue
        _apply_migration(connection, migration)
        applied_now.append(migration.version)
    return applied_now


def schema_version(connection: sqlite3.Connection) -> int:
    """Return the latest applied schema version."""

    _ensure_migration_table(connection)
    row = connection.execute("SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations").fetchone()
    return int(row["version"])


def _ensure_migration_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
        """,
    )
    connection.commit()


def _applied_versions(connection: sqlite3.Connection) -> set[int]:
    rows = connection.execute("SELECT version FROM schema_migrations").fetchall()
    return {int(row["version"]) for row in rows}


def _apply_migration(connection: sqlite3.Connection, migration: Migration) -> None:
    try:
        connection.execute("BEGIN")
        connection.executescript(migration.sql)
        connection.execute(
            "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
            (migration.version, migration.name),
        )
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()


class Storage:
    """Repository bundle for a migrated SQLite database."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.source_items = SourceItemRepository(connection)
        self.media_items = MediaItemRepository(connection)
        self.media_annotations = MediaAnnotationRepository(connection)
        self.line_messages = LineMessageRepository(connection)
        self.notes = NoteRepository(connection)
        self.entities = EntityRepository(connection)
        self.events = EventRepository(connection)
        self.evidence_links = EvidenceLinkRepository(connection)
        self.embeddings = EmbeddingRepository(connection)
        self.text_annotations = TextAnnotationRepository(connection)
        self.audit_log = AuditLogRepository(connection)

    @classmethod
    def open(cls, db_path: Path | str) -> "Storage":
        return initialize_database(db_path)

    def close(self) -> None:
        self.connection.close()

    @property
    def schema_version(self) -> int:
        return schema_version(self.connection)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            self.connection.execute("BEGIN")
            yield self.connection
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()
