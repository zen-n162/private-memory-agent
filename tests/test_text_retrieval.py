import json
from pathlib import Path

from private_memory_agent.cli import main
from private_memory_agent.ingestion.line import ingest_line_exports
from private_memory_agent.ingestion.notes import ingest_notes
from private_memory_agent.retrieval import index_text, search_text
from private_memory_agent.storage import initialize_database


FIXTURE_DIR = Path(__file__).parent / "fixtures"
LINE_FIXTURE = FIXTURE_DIR / "line_export_japanese.txt"
NOTES_FIXTURE_DIR = FIXTURE_DIR / "notes"


def seed_text_database(db_path: Path) -> None:
    ingest_line_exports(LINE_FIXTURE, db_path=db_path)
    ingest_notes(NOTES_FIXTURE_DIR, db_path=db_path)


def seed_photo_annotation(db_path: Path, *, text: str = "屋外で外出した写真") -> int:
    storage = initialize_database(db_path)
    try:
        source_id = storage.source_items.insert_source(
            source_type="photo",
            source_uri="fixture://photo",
            content_sha256="photo-sha",
        )
        media_id = storage.media_items.insert_media(
            source_item_id=source_id,
            media_type="image",
            file_path="fixture://private-photo.jpg",
            sha256="photo-sha",
            mime_type="image/jpeg",
        )
        storage.media_annotations.insert(
            {
                "media_item_id": media_id,
                "annotation_type": "vision",
                "source": "model",
                "value_text": text,
                "data_json": json.dumps(
                    {"objects": ["屋外"], "summary": "外出の記録"},
                    ensure_ascii=False,
                ),
                "model_id": "fake-vl",
            },
        )
        return media_id
    finally:
        storage.close()


def test_index_text_builds_documents_for_line_and_notes(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    seed_text_database(db_path)

    result = index_text(db_path)

    storage = initialize_database(db_path)
    try:
        rows = storage.connection.execute(
            "SELECT source_table, COUNT(*) AS count FROM text_search_documents GROUP BY source_table",
        ).fetchall()
        counts = {row["source_table"]: row["count"] for row in rows}
        line_normalized = storage.connection.execute(
            "SELECT normalized_text FROM line_messages WHERE body_text LIKE '%こんにちは%'",
        ).fetchone()
        note_normalized = storage.connection.execute(
            "SELECT normalized_text FROM notes WHERE title = '研究メモ'",
        ).fetchone()

        assert result.documents_indexed == counts["line_messages"] + counts["notes"]
        assert counts["line_messages"] > 0
        assert counts["notes"] == 4
        assert "こんにちは" in line_normalized["normalized_text"]
        assert "ローカル" in note_normalized["normalized_text"]
    finally:
        storage.close()


def test_search_text_finds_japanese_note_body(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    seed_text_database(db_path)
    index_text(db_path)

    results = search_text(db_path, "ローカル", limit=5)

    assert results
    first = results[0]
    assert first.source_table == "notes"
    assert first.source_id > 0
    assert "ローカル" in first.snippet
    assert len(first.snippet) <= 110
    assert "japanese_frontmatter.md" not in first.snippet


def test_search_text_finds_japanese_line_message(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    seed_text_database(db_path)
    index_text(db_path)

    results = search_text(db_path, "こんにちは", limit=5)

    assert results
    assert results[0].source_table == "line_messages"
    assert "こんにちは" in results[0].snippet
    assert "line_export_japanese.txt" not in results[0].snippet


def test_index_text_includes_media_annotations_for_photo_retrieval(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    media_id = seed_photo_annotation(db_path)

    result = index_text(db_path)
    results = search_text(db_path, "外出に関係しそうな記録を探してください", limit=5)

    assert result.documents_indexed == 1
    assert results
    assert results[0].source_table == "media_items"
    assert results[0].source_id == media_id
    assert "private-photo" not in results[0].snippet


def test_search_text_keyword_like_fallback_finds_japanese_substring(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    seed_text_database(db_path)
    index_text(db_path)

    results = search_text(db_path, "研究に関係しそうなメモやLINEの記録を探してください", limit=5)

    assert results
    assert any(result.source_table in {"notes", "line_messages"} for result in results)


def test_search_text_source_table_filter_recovers_note_candidates(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        for index in range(20):
            storage.line_messages.insert_message(
                source_item_id=None,
                conversation_id="fixture",
                message_id=f"line-{index}",
                sender_id="speaker",
                sent_at="2026-05-24T09:00:00",
                message_type="text",
                body_text="研究の話",
            )
        note_id = storage.notes.insert_note(
            source_item_id=None,
            note_id="note-1",
            title="研究メモ",
            body_text="研究に関係するメモ",
        )
    finally:
        storage.close()
    index_text(db_path)

    global_results = search_text(
        db_path,
        "研究に関係するメモを探してください",
        limit=5,
    )
    note_results = search_text(
        db_path,
        "研究に関係するメモを探してください",
        limit=5,
        source_tables=("notes",),
    )

    assert global_results
    assert note_results
    assert note_results[0].source_table == "notes"
    assert note_results[0].source_id == note_id


def test_search_text_returns_empty_for_blank_query(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    seed_text_database(db_path)
    index_text(db_path)

    assert search_text(db_path, "   ") == []


def test_text_index_and_search_cli(capsys, tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    seed_text_database(db_path)

    index_exit = main(["index", "text", "--db", str(db_path)])
    index_output = capsys.readouterr().out
    search_exit = main(["search", "text", "ローカル", "--db", str(db_path), "--limit", "3"])
    search_output = capsys.readouterr().out

    payload = json.loads(search_output)
    assert index_exit == 0
    assert "Text index complete" in index_output
    assert "documents_indexed=" in index_output
    assert search_exit == 0
    assert payload["query"] == "ローカル"
    assert payload["results"]
    assert payload["results"][0]["source_table"] == "notes"
    assert "ローカル" in payload["results"][0]["snippet"]
    assert "japanese_frontmatter.md" not in search_output


def test_text_index_cli_accepts_local_config_overlay(
    capsys,
    temp_config_factory,
    tmp_path,
):
    db_path = tmp_path / "metadata.sqlite3"
    seed_text_database(db_path)
    config_dir = temp_config_factory(local_paths_yaml="app_data_dir: data/local")

    index_exit = main(
        [
            "index",
            "text",
            "--db",
            str(db_path),
            "--config",
            str(config_dir / "paths.local.yaml"),
        ],
    )
    output = capsys.readouterr().out

    assert index_exit == 0
    assert "Text index complete" in output


def test_search_text_cli_accepts_local_config_overlay(
    capsys,
    temp_config_factory,
    tmp_path,
):
    db_path = tmp_path / "metadata.sqlite3"
    seed_text_database(db_path)
    index_text(db_path)
    config_dir = temp_config_factory(local_paths_yaml="app_data_dir: data/local")

    search_exit = main(
        [
            "search",
            "text",
            "研究",
            "--db",
            str(db_path),
            "--config",
            str(config_dir / "paths.local.yaml"),
        ],
    )
    search_output = capsys.readouterr().out

    payload = json.loads(search_output)
    assert search_exit == 0
    assert payload["query"] == "研究"
    assert payload["results"]
