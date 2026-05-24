"""Small repository classes for SQLite metadata tables."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any


def utc_now() -> str:
    """Return a compact UTC timestamp suitable for SQLite text fields."""

    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class SQLiteRepository:
    """Base repository with basic insert/get/list operations."""

    table_name: str

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self._columns: set[str] | None = None

    @property
    def columns(self) -> set[str]:
        if self._columns is None:
            rows = self.connection.execute(f"PRAGMA table_info({self.table_name})").fetchall()
            self._columns = {str(row["name"]) for row in rows}
        return self._columns

    @property
    def supports_exclusion(self) -> bool:
        return "is_excluded" in self.columns

    def insert(self, values: dict[str, Any]) -> int:
        """Insert a row and return its primary key."""

        row = self._prepare_insert_values(values)
        columns = list(row)
        placeholders = ", ".join("?" for _ in columns)
        column_sql = ", ".join(columns)
        already_in_transaction = self.connection.in_transaction
        cursor = self.connection.execute(
            f"INSERT INTO {self.table_name} ({column_sql}) VALUES ({placeholders})",
            [row[column] for column in columns],
        )
        if not already_in_transaction:
            self.connection.commit()
        return int(cursor.lastrowid)

    def get(self, row_id: int, *, include_excluded: bool = False) -> dict[str, Any] | None:
        where = "id = ?"
        params: list[Any] = [row_id]
        if self.supports_exclusion and not include_excluded:
            where += " AND is_excluded = 0"
        row = self.connection.execute(
            f"SELECT * FROM {self.table_name} WHERE {where}",
            params,
        ).fetchone()
        return _row_to_dict(row)

    def list(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        include_excluded: bool = False,
    ) -> list[dict[str, Any]]:
        where = ""
        if self.supports_exclusion and not include_excluded:
            where = "WHERE is_excluded = 0"
        rows = self.connection.execute(
            f"SELECT * FROM {self.table_name} {where} ORDER BY id LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [_row_to_dict(row) for row in rows if row is not None]

    def mark_excluded(self, row_id: int, *, reason: str | None = None) -> bool:
        if not self.supports_exclusion:
            return False
        now = utc_now()
        already_in_transaction = self.connection.in_transaction
        cursor = self.connection.execute(
            f"""
            UPDATE {self.table_name}
            SET is_excluded = 1,
                excluded_at = ?,
                excluded_reason = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (now, reason, now, row_id),
        )
        if not already_in_transaction:
            self.connection.commit()
        return cursor.rowcount > 0

    def _prepare_insert_values(self, values: dict[str, Any]) -> dict[str, Any]:
        unknown_columns = set(values) - self.columns
        if unknown_columns:
            unknown = ", ".join(sorted(unknown_columns))
            raise ValueError(f"Unknown columns for {self.table_name}: {unknown}")

        now = utc_now()
        row = dict(values)
        if "created_at" in self.columns:
            row.setdefault("created_at", now)
        if "updated_at" in self.columns:
            row.setdefault("updated_at", now)
        if "metadata_json" in self.columns:
            row.setdefault("metadata_json", "{}")
        if "data_json" in self.columns:
            row.setdefault("data_json", "{}")
        if "detail_json" in self.columns:
            row.setdefault("detail_json", "{}")
        if (
            self.table_name == "embeddings"
            and "source_type" in self.columns
            and "owner_table" in row
        ):
            row.setdefault("source_type", _source_type_from_owner_table(str(row["owner_table"])))
        return row


class SourceItemRepository(SQLiteRepository):
    table_name = "source_items"

    def insert_source(
        self,
        *,
        source_type: str,
        source_uri: str,
        external_id: str | None = None,
        content_sha256: str | None = None,
        title: str | None = None,
        metadata_json: str = "{}",
    ) -> int:
        return self.insert(
            {
                "source_type": source_type,
                "source_uri": source_uri,
                "external_id": external_id,
                "content_sha256": content_sha256,
                "title": title,
                "metadata_json": metadata_json,
            },
        )

    def get_by_source_uri(
        self,
        *,
        source_type: str,
        source_uri: str,
        include_excluded: bool = False,
    ) -> dict[str, Any] | None:
        where = "source_type = ? AND source_uri = ?"
        params: list[Any] = [source_type, source_uri]
        if not include_excluded:
            where += " AND is_excluded = 0"
        row = self.connection.execute(
            f"SELECT * FROM {self.table_name} WHERE {where} LIMIT 1",
            params,
        ).fetchone()
        return _row_to_dict(row)

    def get_by_sha256(
        self,
        *,
        source_type: str,
        content_sha256: str,
        include_excluded: bool = False,
    ) -> dict[str, Any] | None:
        where = "source_type = ? AND content_sha256 = ?"
        params: list[Any] = [source_type, content_sha256]
        if not include_excluded:
            where += " AND is_excluded = 0"
        row = self.connection.execute(
            f"SELECT * FROM {self.table_name} WHERE {where} ORDER BY id LIMIT 1",
            params,
        ).fetchone()
        return _row_to_dict(row)


class MediaItemRepository(SQLiteRepository):
    table_name = "media_items"

    def insert_media(
        self,
        *,
        source_item_id: int,
        media_type: str,
        file_path: str | None = None,
        sha256: str | None = None,
        mime_type: str | None = None,
        file_size_bytes: int | None = None,
        width: int | None = None,
        height: int | None = None,
        taken_at: str | None = None,
        modified_at: str | None = None,
        metadata_json: str = "{}",
    ) -> int:
        return self.insert(
            {
                "source_item_id": source_item_id,
                "media_type": media_type,
                "file_path": file_path,
                "sha256": sha256,
                "mime_type": mime_type,
                "file_size_bytes": file_size_bytes,
                "width": width,
                "height": height,
                "taken_at": taken_at,
                "modified_at": modified_at,
                "metadata_json": metadata_json,
            },
        )

    def get_by_sha256(
        self,
        sha256: str,
        *,
        include_excluded: bool = False,
    ) -> dict[str, Any] | None:
        where = "sha256 = ?"
        params: list[Any] = [sha256]
        if not include_excluded:
            where += " AND is_excluded = 0"
        row = self.connection.execute(
            f"SELECT * FROM {self.table_name} WHERE {where} ORDER BY id LIMIT 1",
            params,
        ).fetchone()
        return _row_to_dict(row)


class MediaAnnotationRepository(SQLiteRepository):
    table_name = "media_annotations"


class LineMessageRepository(SQLiteRepository):
    table_name = "line_messages"

    def insert_message(
        self,
        *,
        source_item_id: int | None,
        conversation_id: str | None,
        message_id: str,
        sender_id: str | None,
        sent_at: str | None,
        message_type: str,
        body_text: str,
        normalized_text: str | None = None,
        metadata_json: str = "{}",
    ) -> int:
        return self.insert(
            {
                "source_item_id": source_item_id,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "sender_id": sender_id,
                "sent_at": sent_at,
                "message_type": message_type,
                "body_text": body_text,
                "normalized_text": normalized_text,
                "metadata_json": metadata_json,
            },
        )

    def get_by_message_id(
        self,
        *,
        source_item_id: int,
        message_id: str,
        include_excluded: bool = False,
    ) -> dict[str, Any] | None:
        where = "source_item_id = ? AND message_id = ?"
        params: list[Any] = [source_item_id, message_id]
        if not include_excluded:
            where += " AND is_excluded = 0"
        row = self.connection.execute(
            f"SELECT * FROM {self.table_name} WHERE {where} ORDER BY id LIMIT 1",
            params,
        ).fetchone()
        return _row_to_dict(row)


class NoteRepository(SQLiteRepository):
    table_name = "notes"

    def insert_note(
        self,
        *,
        source_item_id: int | None,
        note_id: str,
        title: str | None,
        body_text: str | None,
        created_at_source: str | None = None,
        updated_at_source: str | None = None,
        normalized_text: str | None = None,
        metadata_json: str = "{}",
    ) -> int:
        return self.insert(
            {
                "source_item_id": source_item_id,
                "note_id": note_id,
                "title": title,
                "body_text": body_text,
                "created_at_source": created_at_source,
                "updated_at_source": updated_at_source,
                "normalized_text": normalized_text,
                "metadata_json": metadata_json,
            },
        )

    def get_by_note_id(
        self,
        note_id: str,
        *,
        include_excluded: bool = False,
    ) -> dict[str, Any] | None:
        where = "note_id = ?"
        params: list[Any] = [note_id]
        if not include_excluded:
            where += " AND is_excluded = 0"
        row = self.connection.execute(
            f"SELECT * FROM {self.table_name} WHERE {where} ORDER BY id LIMIT 1",
            params,
        ).fetchone()
        return _row_to_dict(row)


class EntityRepository(SQLiteRepository):
    table_name = "entities"

    def insert_entity(
        self,
        *,
        entity_type: str,
        canonical_name: str | None = None,
        display_name: str | None = None,
        metadata_json: str = "{}",
    ) -> int:
        return self.insert(
            {
                "entity_type": entity_type,
                "canonical_name": canonical_name,
                "display_name": display_name,
                "metadata_json": metadata_json,
            },
        )

    def update_entity(
        self,
        row_id: int,
        *,
        canonical_name: str | None = None,
        display_name: str | None = None,
        metadata_json: str | None = None,
    ) -> bool:
        values: dict[str, Any] = {"updated_at": utc_now()}
        if canonical_name is not None:
            values["canonical_name"] = canonical_name
        if display_name is not None:
            values["display_name"] = display_name
        if metadata_json is not None:
            values["metadata_json"] = metadata_json
        assignments = ", ".join(f"{column} = ?" for column in values)
        cursor = self.connection.execute(
            f"UPDATE entities SET {assignments} WHERE id = ?",
            (*values.values(), row_id),
        )
        if not self.connection.in_transaction:
            self.connection.commit()
        return cursor.rowcount > 0


class EventRepository(SQLiteRepository):
    table_name = "events"

    def insert_event(
        self,
        *,
        event_type: str,
        title: str | None,
        description: str | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        confidence: float | None = None,
        metadata_json: str = "{}",
    ) -> int:
        return self.insert(
            {
                "event_type": event_type,
                "title": title,
                "description": description,
                "started_at": started_at,
                "ended_at": ended_at,
                "confidence": confidence,
                "metadata_json": metadata_json,
            },
        )


class EvidenceLinkRepository(SQLiteRepository):
    table_name = "evidence_links"

    def insert_link(
        self,
        *,
        target_table: str,
        target_id: int,
        evidence_table: str,
        evidence_id: int,
        relation_type: str = "supports",
        weight: float | None = None,
        metadata_json: str = "{}",
    ) -> int:
        return self.insert(
            {
                "target_table": target_table,
                "target_id": target_id,
                "evidence_table": evidence_table,
                "evidence_id": evidence_id,
                "relation_type": relation_type,
                "weight": weight,
                "metadata_json": metadata_json,
            },
        )

    def exists(
        self,
        *,
        target_table: str,
        target_id: int,
        evidence_table: str,
        evidence_id: int,
        relation_type: str = "supports",
    ) -> bool:
        row = self.connection.execute(
            """
            SELECT 1
            FROM evidence_links
            WHERE target_table = ?
              AND target_id = ?
              AND evidence_table = ?
              AND evidence_id = ?
              AND relation_type = ?
              AND is_excluded = 0
            LIMIT 1
            """,
            (target_table, target_id, evidence_table, evidence_id, relation_type),
        ).fetchone()
        return row is not None


class EmbeddingRepository(SQLiteRepository):
    table_name = "embeddings"

    def insert_embedding(
        self,
        *,
        owner_table: str,
        owner_id: int,
        embedding_type: str,
        model_id: str,
        dimensions: int,
        vector_json: str,
        metadata_json: str = "{}",
    ) -> int:
        values = {
            "owner_table": owner_table,
            "owner_id": owner_id,
            "embedding_type": embedding_type,
            "model_id": model_id,
            "dimensions": dimensions,
            "vector_json": vector_json,
            "metadata_json": metadata_json,
        }
        return self.insert(values)


class TextAnnotationRepository(SQLiteRepository):
    table_name = "text_annotations"

    def insert_text_annotation(
        self,
        *,
        source_table: str,
        source_id: int,
        annotation_type: str,
        model_id: str,
        summary: str | None,
        entities_json: str,
        topics_json: str,
        dates_json: str,
        action_items_json: str,
        event_hints_json: str,
        confidence: float | None,
        raw_json: str = "{}",
    ) -> int:
        return self.insert(
            {
                "source_table": source_table,
                "source_id": source_id,
                "annotation_type": annotation_type,
                "model_id": model_id,
                "summary": summary,
                "entities_json": entities_json,
                "topics_json": topics_json,
                "dates_json": dates_json,
                "action_items_json": action_items_json,
                "event_hints_json": event_hints_json,
                "confidence": confidence,
                "raw_json": raw_json,
            },
        )


class AuditLogRepository(SQLiteRepository):
    table_name = "audit_log"


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def _source_type_from_owner_table(owner_table: str) -> str:
    return {
        "line_messages": "line",
        "notes": "notes",
        "media_items": "photos",
        "media_annotations": "photos",
    }.get(owner_table, owner_table)
