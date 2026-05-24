import sqlite3

import pytest

from private_memory_agent.storage import Storage, initialize_database
from private_memory_agent.storage.database import apply_migrations, connect, schema_version


REQUIRED_TABLES = {
    "source_items",
    "media_items",
    "media_annotations",
    "line_messages",
    "notes",
    "entities",
    "events",
    "evidence_links",
    "embeddings",
    "text_annotations",
    "audit_log",
    "text_search_documents",
    "schema_migrations",
}


def test_initialize_database_applies_schema(tmp_path):
    storage = initialize_database(tmp_path / "metadata.sqlite3")
    try:
        rows = storage.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'",
        ).fetchall()
        table_names = {row["name"] for row in rows}

        assert REQUIRED_TABLES <= table_names
        view_rows = storage.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'view'",
        ).fetchall()
        view_names = {row["name"] for row in view_rows}

        assert "text_documents" in view_names
        assert storage.schema_version == 4
    finally:
        storage.close()


def test_migrations_are_idempotent(tmp_path):
    connection = connect(tmp_path / "metadata.sqlite3")
    try:
        first = apply_migrations(connection)
        second = apply_migrations(connection)

        assert first == [1, 2, 3, 4]
        assert second == []
        assert schema_version(connection) == 4
    finally:
        connection.close()


def test_source_and_media_repository_insert_get_list_and_exclude(tmp_path):
    storage = initialize_database(tmp_path / "metadata.sqlite3")
    try:
        source_id = storage.source_items.insert_source(
            source_type="photo",
            source_uri="fixture://photo-1",
            content_sha256="abc123",
            title="synthetic item",
        )
        media_id = storage.media_items.insert_media(
            source_item_id=source_id,
            media_type="image",
            file_path="fixture://photo-1.png",
            sha256="abc123",
            mime_type="image/png",
            file_size_bytes=70,
            width=1,
            height=1,
        )

        source = storage.source_items.get(source_id)
        media = storage.media_items.get(media_id)

        assert source["source_type"] == "photo"
        assert source["created_at"].endswith("Z")
        assert source["updated_at"].endswith("Z")
        assert media["source_item_id"] == source_id
        assert media["width"] == 1
        assert storage.media_items.list()[0]["id"] == media_id

        assert storage.media_items.mark_excluded(media_id, reason="synthetic removal")
        assert storage.media_items.get(media_id) is None
        assert storage.media_items.get(media_id, include_excluded=True)["is_excluded"] == 1
        assert storage.media_items.list() == []
        assert len(storage.media_items.list(include_excluded=True)) == 1
    finally:
        storage.close()


def test_source_items_are_unique_by_type_and_uri(tmp_path):
    storage = initialize_database(tmp_path / "metadata.sqlite3")
    try:
        storage.source_items.insert_source(source_type="photo", source_uri="fixture://same")

        with pytest.raises(sqlite3.IntegrityError):
            storage.source_items.insert_source(source_type="photo", source_uri="fixture://same")
    finally:
        storage.close()


def test_all_table_repositories_support_basic_insert_get_list(tmp_path):
    storage = initialize_database(tmp_path / "metadata.sqlite3")
    try:
        source_id = storage.source_items.insert_source(source_type="fixture", source_uri="fixture://root")
        media_id = storage.media_items.insert_media(source_item_id=source_id, media_type="image")

        cases = [
            (storage.media_annotations, {"media_item_id": media_id, "annotation_type": "caption"}),
            (
                storage.line_messages,
                {"source_item_id": source_id, "conversation_id": "c1", "message_id": "m1"},
            ),
            (storage.notes, {"source_item_id": source_id, "note_id": "n1", "title": "synthetic"}),
            (storage.entities, {"entity_type": "person", "canonical_name": "synthetic"}),
            (storage.events, {"event_type": "synthetic", "title": "fixture event"}),
            (
                storage.evidence_links,
                {
                    "target_table": "events",
                    "target_id": 1,
                    "evidence_table": "media_items",
                    "evidence_id": media_id,
                },
            ),
            (
                storage.embeddings,
                {
                    "owner_table": "notes",
                    "owner_id": 1,
                    "embedding_type": "text",
                    "dimensions": 3,
                    "vector_json": "[0.0, 0.0, 0.0]",
                },
            ),
            (
                storage.text_annotations,
                {
                    "source_table": "notes",
                    "source_id": 1,
                    "annotation_type": "understanding",
                    "model_id": "fixture-model",
                    "summary": "synthetic",
                },
            ),
            (storage.audit_log, {"action": "test.insert", "actor": "pytest"}),
        ]

        for repository, values in cases:
            row_id = repository.insert(values)
            row = repository.get(row_id)
            rows = repository.list()

            assert row is not None
            assert row["id"] == row_id
            assert rows
            assert rows[-1]["id"] == row_id
            assert row["created_at"].endswith("Z")
            assert row["updated_at"].endswith("Z")
            if repository is storage.embeddings:
                assert row["source_type"] == "notes"
    finally:
        storage.close()


def test_sql_compatibility_view_and_embedding_source_type(tmp_path):
    storage = initialize_database(tmp_path / "metadata.sqlite3")
    try:
        source_id = storage.source_items.insert_source(
            source_type="photo",
            source_uri="fixture://photo",
        )
        media_id = storage.media_items.insert_media(
            source_item_id=source_id,
            media_type="image",
        )
        storage.embeddings.insert_embedding(
            owner_table="media_items",
            owner_id=media_id,
            embedding_type="text",
            model_id="fake",
            dimensions=2,
            vector_json="[1,0]",
        )
        storage.connection.execute(
            """
            INSERT INTO text_search_documents(
                source_table,
                source_id,
                title,
                body,
                normalized_text,
                snippet_text,
                indexed_at
            )
            VALUES ('media_items', ?, NULL, 'synthetic photo text', 'synthetic photo text', 'synthetic photo text', ?)
            """,
            (media_id, "2026-05-24T00:00:00Z"),
        )
        storage.connection.commit()

        text_rows = storage.connection.execute(
            "SELECT source_type, COUNT(*) AS count FROM text_documents GROUP BY source_type",
        ).fetchall()
        embedding_rows = storage.connection.execute(
            "SELECT source_type, COUNT(*) AS count FROM embeddings GROUP BY source_type",
        ).fetchall()
        photo_count = storage.connection.execute(
            "SELECT COUNT(*) AS count FROM text_documents WHERE source_type LIKE '%photo%' OR source_type LIKE '%media%'",
        ).fetchone()

        assert {row["source_type"]: row["count"] for row in text_rows} == {"photos": 1}
        assert {row["source_type"]: row["count"] for row in embedding_rows} == {"photos": 1}
        assert photo_count["count"] == 1
    finally:
        storage.close()


def test_repository_rejects_unknown_columns(tmp_path):
    storage = initialize_database(tmp_path / "metadata.sqlite3")
    try:
        with pytest.raises(ValueError, match="Unknown columns"):
            storage.source_items.insert({"source_type": "photo", "source_uri": "x", "raw_payload": "nope"})
    finally:
        storage.close()


def test_storage_transaction_rolls_back(tmp_path):
    storage = initialize_database(tmp_path / "metadata.sqlite3")
    try:
        with pytest.raises(RuntimeError):
            with storage.transaction():
                storage.source_items.insert_source(
                    source_type="fixture",
                    source_uri="fixture://rolled-back",
                )
                raise RuntimeError("force rollback")

        assert storage.source_items.list() == []
    finally:
        storage.close()
