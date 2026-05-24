import json
import sqlite3

from private_memory_agent.cli import main
from private_memory_agent.db_diagnostics import (
    inspect_database_schema,
    inspect_source_coverage,
)
from private_memory_agent.e2e import E2ESmokeOptions, run_e2e_smoke
from private_memory_agent.retrieval import index_text
from private_memory_agent.storage import initialize_database


def _write_smoke_query(config_dir, *, text="研究", sources="line,notes"):
    (config_dir / "e2e_smoke_queries.local.yaml").write_text(
        "\n".join(
            [
                "queries:",
                "  local:",
                f"    text: \"{text}\"",
                f"    sources: {sources}",
            ],
        )
        + "\n",
        encoding="utf-8",
    )


def _insert_photo_annotation(storage, *, path="/private/secret-photo.jpg", text="外出"):
    source_id = storage.source_items.insert_source(
        source_type="photo",
        source_uri="fixture://secret-photo",
        content_sha256="photo-sha",
    )
    media_id = storage.media_items.insert_media(
        source_item_id=source_id,
        media_type="image",
        file_path=path,
        sha256="photo-sha",
        mime_type="image/jpeg",
    )
    storage.media_annotations.insert(
        {
            "media_item_id": media_id,
            "annotation_type": "vision",
            "source": "model",
            "value_text": text,
            "model_id": "fake-vl",
        },
    )
    return media_id


def test_db_schema_command_reports_actual_schema_without_private_rows(
    capsys,
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    db_path = tmp_path / "metadata.sqlite3"
    private_path = "/private/Alice-secret-photo.jpg"
    storage = initialize_database(db_path)
    try:
        _insert_photo_annotation(storage, path=private_path, text="secret caption")
    finally:
        storage.close()

    exit_code = main(
        [
            "db",
            "schema",
            "--db",
            str(db_path),
            "--config-dir",
            str(config_dir),
            "--json",
        ],
    )
    output = capsys.readouterr().out
    payload = json.loads(output)
    table_names = {table["name"] for table in payload["tables"]}

    assert exit_code == 0
    assert "media_items" in table_names
    assert "text_search_documents" in table_names
    assert "text_documents" not in table_names
    assert "file_path" in next(table for table in payload["tables"] if table["name"] == "media_items")["columns"]
    assert private_path not in output
    assert "secret caption" not in output
    assert str(tmp_path) not in output


def test_schema_report_handles_missing_text_documents_table(tmp_path):
    db_path = tmp_path / "raw.sqlite3"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("CREATE TABLE line_messages(id INTEGER PRIMARY KEY, body_text TEXT)")
        connection.commit()
    finally:
        connection.close()

    report = inspect_database_schema(db_path)
    payload = report.to_dict()
    table_names = {table["name"] for table in payload["tables"]}

    assert report.db_exists is True
    assert "line_messages" in table_names
    assert "text_documents" not in table_names
    assert "text_search_documents" not in table_names


def test_source_coverage_labels_physical_text_index_and_embedding_sources(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        line_id = storage.line_messages.insert_message(
            source_item_id=None,
            conversation_id="fixture",
            message_id="line-1",
            sender_id="speaker",
            sent_at="2026-05-24T09:00:00",
            message_type="text",
            body_text="研究の話",
        )
        note_id = storage.notes.insert_note(
            source_item_id=None,
            note_id="note-1",
            title="研究",
            body_text="研究メモ",
        )
        storage.embeddings.insert_embedding(
            owner_table="line_messages",
            owner_id=line_id,
            embedding_type="text",
            model_id="fake",
            dimensions=2,
            vector_json="[1,0]",
        )
        storage.embeddings.insert_embedding(
            owner_table="notes",
            owner_id=note_id,
            embedding_type="text",
            model_id="fake",
            dimensions=2,
            vector_json="[0,1]",
        )
    finally:
        storage.close()
    index_text(db_path)

    coverage = inspect_source_coverage(db_path)

    assert coverage.text.text_documents_table == "text_search_documents"
    assert coverage.text.text_documents_count_kind == "physical_table"
    assert coverage.text.text_documents_source_breakdown["line_messages"] == 1
    assert coverage.text.text_documents_source_breakdown["notes"] == 1
    assert coverage.embeddings.embedding_source_breakdown_available is True
    assert coverage.embeddings.embedding_source_breakdown == {"line": 1, "notes": 1}
    assert coverage.embeddings.embedding_model_breakdown == {"fake": 2}
    assert coverage.embeddings.embedding_model_source_breakdown == {
        "fake": {"line": 1, "notes": 1},
    }


def test_embedding_source_breakdown_reports_unavailable_when_no_mapping(tmp_path):
    db_path = tmp_path / "raw.sqlite3"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "CREATE TABLE embeddings(id INTEGER PRIMARY KEY, vector_json TEXT, is_excluded INTEGER DEFAULT 0)",
        )
        connection.execute("INSERT INTO embeddings(vector_json) VALUES ('[0]')")
        connection.commit()
    finally:
        connection.close()

    coverage = inspect_source_coverage(db_path)

    assert coverage.embeddings.embeddings_table_exists is True
    assert coverage.embeddings.embeddings_count == 1
    assert coverage.embeddings.embedding_source_breakdown_available is False
    assert "does not store source_type" in coverage.embeddings.reason


def test_retrieval_audit_reports_selected_embedding_model_coverage(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        note_id = storage.notes.insert_note(
            source_item_id=None,
            note_id="audit-note",
            title="研究",
            body_text="研究メモ",
        )
        storage.embeddings.insert_embedding(
            owner_table="notes",
            owner_id=note_id,
            embedding_type="text",
            model_id="ruri-v3-310m",
            dimensions=2,
            vector_json="[1,0]",
        )
    finally:
        storage.close()
    from private_memory_agent.db_diagnostics import run_retrieval_audit

    report = run_retrieval_audit(
        db_path,
        selected_semantic_model_id="ruri-v3-310m",
    )

    assert report.selected_semantic_model_id == "ruri-v3-310m"
    assert report.selected_semantic_model_has_embeddings is True


def test_media_annotation_diagnostics_report_direct_retrieval_not_text_index(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_photo_annotation(storage, text="外出")
    finally:
        storage.close()

    coverage = inspect_source_coverage(db_path)

    assert coverage.media_annotations.media_annotations_count == 1
    assert coverage.media_annotations.media_annotations_in_text_index_count == 0
    assert coverage.media_annotations.media_annotations_searchable is True
    assert coverage.media_annotations.media_annotations_searchable_via == (
        "direct_media_annotation_retrieval",
    )
    assert coverage.media_annotations.photo_evidence_retrievable is True


def test_media_annotation_diagnostics_report_text_index_after_indexing(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_photo_annotation(storage, text="屋外で外出")
    finally:
        storage.close()

    index_text(db_path)
    coverage = inspect_source_coverage(db_path)

    assert coverage.media_annotations.media_annotations_in_text_index_count == 1
    assert "text_search_documents" in coverage.media_annotations.media_annotations_searchable_via
    assert coverage.text.text_documents_source_breakdown["media_items"] == 1


def test_e2e_no_fallback_does_not_count_inventory_as_success(
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    _write_smoke_query(config_dir, text="一致しない検索語", sources="line")
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_photo_annotation(storage, text="外出")
    finally:
        storage.close()

    report = run_e2e_smoke(
        E2ESmokeOptions(
            config_dir=config_dir,
            db_path=db_path,
            retrieval_only=True,
            no_fallback=True,
        ),
    )

    assert report.ok is False
    assert all(not result.used_inventory_fallback for result in report.query_results)
    assert report.source_coverage.fallback_evidence_count == 0
    assert report.source_coverage.queries_with_zero_evidence == 1
    assert report.query_results[0].retrieval_stage_counts["final_evidence_count"] == 0


def test_retrieve_audit_json_is_schema_aware_and_privacy_safe(
    capsys,
    temp_config_factory,
    tmp_path,
):
    config_dir = temp_config_factory()
    _write_smoke_query(config_dir, text="研究", sources="line,notes")
    db_path = tmp_path / "metadata.sqlite3"
    private_text = "研究 private secret note body"
    storage = initialize_database(db_path)
    try:
        storage.line_messages.insert_message(
            source_item_id=None,
            conversation_id="fixture",
            message_id="line-1",
            sender_id="speaker",
            sent_at="2026-05-24T09:00:00",
            message_type="text",
            body_text=private_text,
        )
    finally:
        storage.close()
    index_text(db_path)

    exit_code = main(
        [
            "retrieve",
            "audit",
            "--db",
            str(db_path),
            "--config-dir",
            str(config_dir),
            "--json",
        ],
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code == 0
    assert payload["source_coverage"]["text"]["text_documents_table"] == "text_search_documents"
    assert payload["query_diagnostics"][0]["fts_candidate_count"] >= 0
    assert payload["query_diagnostics"][0]["exact_like_candidate_count"] >= 0
    assert payload["query_diagnostics"][0]["keyword_like_candidate_count"] >= 0
    assert "notes" in payload["query_diagnostics"][0]["source_stage_counts"]
    assert payload["retrieval_coverage"]["real_line_evidence_count"] == 1
    assert payload["query_diagnostics"][0]["final_evidence_count"] == 1
    assert private_text not in output
    assert "研究" not in output
    assert str(tmp_path) not in output


def test_retrieve_audit_reports_note_stage_counts_and_rank_recovery(tmp_path):
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
                body_text="研究の予定を確認した。",
            )
        storage.notes.insert_note(
            source_item_id=None,
            note_id="note-1",
            title="研究メモ",
            body_text="研究に関係するメモ。",
        )
    finally:
        storage.close()
    index_text(db_path)

    from private_memory_agent.db_diagnostics import run_retrieval_audit

    report = run_retrieval_audit(
        db_path,
        (("mixed", "研究に関係するメモを探してください", ("line", "notes")),),
        limit=5,
    )
    diagnostic = report.query_diagnostics[0]
    note_counts = diagnostic.source_stage_counts["notes"]

    assert note_counts["text_candidate_count"] >= 1
    assert note_counts["candidate_count_after_source_filter"] >= 1
    assert note_counts["evidence_conversion_count"] >= 1
    assert note_counts["drop_reason"] is None
    assert diagnostic.evidence_source_counts["notes"] >= 1
    assert report.retrieval_coverage.real_note_evidence_count >= 1


def test_retrieve_audit_reports_zero_candidate_stage(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    initialize_database(db_path).close()
    index_text(db_path)

    from private_memory_agent.db_diagnostics import run_retrieval_audit

    report = run_retrieval_audit(
        db_path,
        (("empty", "一致しない検索語", ("line", "notes")),),
    )
    diagnostic = report.query_diagnostics[0]

    assert diagnostic.text_candidate_count == 0
    assert diagnostic.media_annotation_candidate_count == 0
    assert diagnostic.candidate_count_after_source_filter == 0
    assert diagnostic.final_evidence_count == 0
    assert "no real evidence" in diagnostic.warnings[0]


def test_retrieve_audit_photo_annotation_is_retrievable_by_keyword(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_photo_annotation(storage, path="/private/secret-outing.jpg", text="屋外で外出した")
    finally:
        storage.close()
    index_text(db_path)

    from private_memory_agent.db_diagnostics import run_retrieval_audit

    report = run_retrieval_audit(
        db_path,
        (("photo", "外出に関係しそうな写真を探してください", ("photos",)),),
    )
    diagnostic = report.query_diagnostics[0]

    assert diagnostic.text_candidate_count >= 1
    assert diagnostic.media_annotation_candidate_count >= 1
    assert diagnostic.final_evidence_count == 1
    assert diagnostic.evidence_source_counts == {"photos": 1}
    assert report.retrieval_coverage.real_photo_evidence_count == 1
