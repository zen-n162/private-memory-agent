import json

from private_memory_agent.cli import main
from private_memory_agent.retrieval import (
    FakeEmbeddingModel,
    FakeEvidenceReranker,
    RetrievalFilters,
    RetrievalService,
    index_embeddings,
    pack_evidence_for_prompt,
)
from private_memory_agent.storage import initialize_database


def seed_evidence_database(db_path):
    storage = initialize_database(db_path)
    try:
        line_id = storage.line_messages.insert_message(
            source_item_id=None,
            conversation_id="fixture-room",
            message_id="line-1",
            sender_id="fixture-speaker",
            sent_at="2026-05-24T09:00:00",
            message_type="text",
            body_text="ローカル検索について話した。",
        )
        note_id = storage.notes.insert_note(
            source_item_id=None,
            note_id="note-1",
            title="研究メモ",
            body_text="ローカル検索と研究計画についてのメモ。",
            created_at_source="2026-05-20T10:00:00",
            updated_at_source="2026-05-21T10:00:00",
        )
        source_id = storage.source_items.insert_source(
            source_type="photo",
            source_uri="fixture://photo-1",
            content_sha256="photo-sha",
        )
        media_id = storage.media_items.insert_media(
            source_item_id=source_id,
            media_type="image",
            file_path="fixture://redacted-photo.png",
            sha256="photo-sha",
            mime_type="image/png",
            width=1,
            height=1,
            taken_at="2026-05-22T12:00:00",
        )
        storage.media_annotations.insert(
            {
                "media_item_id": media_id,
                "annotation_type": "vision",
                "source": "model",
                "value_text": "公園でローカル検索の話をした写真",
                "data_json": json.dumps(
                    {"objects": ["公園", "ノート"], "ocr_text": "秘密OCR"},
                    ensure_ascii=False,
                ),
                "confidence": 0.8,
                "model_id": "fake-vl",
            },
        )
        return {"line_id": line_id, "note_id": note_id, "media_id": media_id}
    finally:
        storage.close()


def test_retrieval_service_combines_text_and_media_annotation_evidence(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    ids = seed_evidence_database(db_path)
    service = RetrievalService(db_path)

    result = service.retrieve("ローカル検索", limit=5, redact_for_display=False)

    evidence_by_kind = {item.source_kind: item for item in result.evidence}
    assert {"line", "notes", "photos"} <= set(evidence_by_kind)
    assert evidence_by_kind["line"].source_id == ids["line_id"]
    assert evidence_by_kind["notes"].source_id == ids["note_id"]
    assert evidence_by_kind["photos"].source_id == ids["media_id"]
    assert all(item.confidence > 0 for item in result.evidence)
    assert "Local evidence:" in result.packed_evidence


def test_retrieval_filters_source_and_date(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    seed_evidence_database(db_path)
    service = RetrievalService(db_path)

    result = service.retrieve(
        "ローカル検索",
        filters=RetrievalFilters(sources=("line",), since="2026-05-23"),
        limit=5,
        redact_for_display=False,
    )

    assert result.evidence
    assert {item.source_kind for item in result.evidence} == {"line"}
    assert all(item.occurred_at >= "2026-05-23" for item in result.evidence)


def test_retrieval_finds_photo_annotation_with_japanese_keyword_query(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    ids = seed_evidence_database(db_path)
    service = RetrievalService(db_path)

    result = service.retrieve(
        "ローカル検索に関係しそうな記録を探してください",
        filters=RetrievalFilters(sources=("photos",)),
        limit=5,
        redact_for_display=True,
    )

    assert result.evidence
    assert result.evidence[0].source_kind == "photos"
    assert result.evidence[0].source_id == ids["media_id"]


def test_retrieval_source_filters_use_text_index_for_all_supported_sources(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    ids = seed_evidence_database(db_path)
    service = RetrievalService(db_path)

    line_notes = service.retrieve(
        "ローカル検索に関係しそうな記録を探してください",
        filters=RetrievalFilters(sources=("line", "notes")),
        limit=5,
        redact_for_display=True,
    )
    photos = service.retrieve(
        "ローカル検索に関係しそうな写真を探してください",
        filters=RetrievalFilters(sources=("photos",)),
        limit=5,
        redact_for_display=True,
    )

    assert {item.source_kind for item in line_notes.evidence} <= {"line", "notes"}
    assert {"line", "notes"} <= {item.source_kind for item in line_notes.evidence}
    assert {item.source_kind for item in photos.evidence} == {"photos"}
    assert any(item.source_id == ids["media_id"] for item in photos.evidence)


def test_retrieval_recovers_notes_when_line_candidates_dominate_index_order(tmp_path):
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
        note_id = storage.notes.insert_note(
            source_item_id=None,
            note_id="note-1",
            title="研究メモ",
            body_text="研究に関係するメモ。",
            created_at_source="2026-05-24T10:00:00",
            updated_at_source="2026-05-24T10:00:00",
        )
    finally:
        storage.close()

    service = RetrievalService(db_path)
    result = service.retrieve(
        "研究に関係するメモを探してください",
        filters=RetrievalFilters(sources=("line", "notes")),
        limit=5,
        redact_for_display=True,
    )

    note_evidence = [item for item in result.evidence if item.source_kind == "notes"]
    assert note_evidence
    assert note_evidence[0].source_table == "notes"
    assert note_evidence[0].source_id == note_id
    assert note_evidence[0].evidence_id == f"notes:{note_id}"


def test_retrieval_deduplicates_fts_and_semantic_signals(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    ids = seed_evidence_database(db_path)
    model = FakeEmbeddingModel(vocabulary=("ローカル", "検索", "研究"))
    index_embeddings(db_path, model)
    service = RetrievalService(db_path, embedding_model=model)

    result = service.retrieve(
        "ローカル検索",
        filters=RetrievalFilters(sources=("notes",)),
        limit=5,
        redact_for_display=False,
    )

    note_results = [item for item in result.evidence if item.source_id == ids["note_id"]]
    assert len(note_results) == 1
    assert set(note_results[0].signals) == {"fts", "semantic"}
    assert result.diagnostics["semantic_candidate_count"] > 0


def test_semantic_retrieval_respects_source_filters(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    ids = seed_evidence_database(db_path)
    model = FakeEmbeddingModel(vocabulary=("ローカル", "検索", "研究"))
    index_embeddings(db_path, model)
    service = RetrievalService(db_path, embedding_model=model)

    result = service.retrieve(
        "ローカル検索",
        filters=RetrievalFilters(sources=("notes",), semantic_top_k=10),
        limit=5,
        redact_for_display=False,
    )

    assert result.evidence
    assert {item.source_kind for item in result.evidence} == {"notes"}
    assert any(item.source_id == ids["note_id"] for item in result.evidence)
    assert result.diagnostics["semantic_candidate_count"] >= 1


def test_retrieval_service_reports_fake_reranker_diagnostics(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    seed_evidence_database(db_path)
    service = RetrievalService(
        db_path,
        reranker=FakeEvidenceReranker(),
        rerank_top_k=3,
    )

    result = service.retrieve("ローカル検索", limit=3, redact_for_display=True)

    assert result.evidence
    assert result.diagnostics["reranked_candidate_count"] == 3


def test_pack_evidence_for_prompt_can_redact_private_text(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    seed_evidence_database(db_path)
    service = RetrievalService(db_path)
    result = service.retrieve("公園", limit=1, redact_for_display=False)

    full = pack_evidence_for_prompt(result.evidence, redact_private=False)
    redacted = pack_evidence_for_prompt(result.evidence, redact_private=True)

    assert "公園" in full
    assert "公園" not in redacted
    assert "[redacted]" in redacted


def test_retrieve_cli_redacts_private_evidence_by_default(capsys, tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    seed_evidence_database(db_path)

    exit_code = main(["retrieve", "ローカル検索", "--db", str(db_path), "--limit", "3"])

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 0
    assert payload["redacted"] is True
    assert payload["evidence"]
    assert "[redacted]" in output
    assert "研究メモ" not in output
    assert "秘密OCR" not in output
    assert "redacted-photo.png" not in output
