import json
from datetime import date

from private_memory_agent.api.console import ChatConsoleOptions, run_chat_console_query
from private_memory_agent.cli import main
from private_memory_agent.storage import initialize_database
from private_memory_agent.temporal import (
    answer_temporal_event_query,
    cluster_photo_candidates_by_day,
    parse_temporal_event_query,
    score_outing_likelihood,
    search_photos_by_date_range,
)


def _insert_photo(
    storage,
    *,
    taken_at,
    annotation_text=None,
    path="/private/secret-photo.jpg",
    metadata=None,
):
    source_id = storage.source_items.insert_source(
        source_type="photo",
        source_uri=f"fixture://{path}-{taken_at}",
        content_sha256=f"sha-{path}-{taken_at}",
    )
    media_id = storage.media_items.insert_media(
        source_item_id=source_id,
        media_type="image",
        file_path=path,
        sha256=f"sha-{path}-{taken_at}",
        mime_type="image/jpeg",
        width=120,
        height=80,
        taken_at=taken_at,
        metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
    )
    if annotation_text is not None:
        storage.media_annotations.insert(
            {
                "media_item_id": media_id,
                "annotation_type": "vision",
                "source": "model",
                "value_text": annotation_text,
                "model_id": "fake-vl",
                "confidence": 0.8,
            },
        )
    return media_id


def _insert_line(storage, *, sent_at="2025-12-03T18:00:00", text="PRIVATE_LINE_TEXT"):
    return storage.line_messages.insert_message(
        source_item_id=None,
        conversation_id="temporal-room",
        message_id=f"line-{sent_at}",
        sender_id="speaker",
        sent_at=sent_at,
        message_type="text",
        body_text=text,
    )


def _insert_note(storage, *, updated_at="2025-12-03T19:00:00", body="PRIVATE_NOTE_BODY"):
    return storage.notes.insert_note(
        source_item_id=None,
        note_id=f"note-{updated_at}",
        title="PRIVATE_TITLE",
        body_text=body,
        created_at_source=updated_at,
        updated_at_source=updated_at,
    )


def test_temporal_parser_understands_japanese_month_and_relative_dates():
    parsed = parse_temporal_event_query(
        "2025年12月で出かけたのはいつ？",
        today=date(2026, 5, 26),
    )

    assert parsed is not None
    assert parsed.query_type == "temporal_event_search"
    assert parsed.event_type == "outing"
    assert parsed.date_range.start.isoformat() == "2025-12-01"
    assert parsed.date_range.end.isoformat() == "2026-01-01"

    last_year = parse_temporal_event_query("去年12月に外出した日は？", today=date(2026, 5, 26))
    assert last_year is not None
    assert last_year.date_range.start.isoformat() == "2025-12-01"

    previous_month = parse_temporal_event_query("先月どこか行った日は？", today=date(2026, 5, 26))
    assert previous_month is not None
    assert previous_month.date_range.start.isoformat() == "2026-04-01"


def test_photo_date_range_search_returns_safe_metadata_only(tmp_path):
    db_path = tmp_path / "temporal.sqlite3"
    storage = initialize_database(db_path)
    try:
        media_id = _insert_photo(
            storage,
            taken_at="2025-12-03T10:00:00",
            annotation_text="駅とカフェが写っている屋外写真",
            path="/private/path/secret-outing.jpg",
        )
        _insert_photo(
            storage,
            taken_at="2026-01-03T10:00:00",
            annotation_text="駅",
        )
    finally:
        storage.close()

    candidates = search_photos_by_date_range(
        db_path,
        start=date(2025, 12, 1),
        end=date(2026, 1, 1),
    )
    serialized = json.dumps([candidate.__dict__ for candidate in candidates], ensure_ascii=False)

    assert [candidate.media_item_id for candidate in candidates] == [media_id]
    assert candidates[0].should_use is True
    assert "/private/path" not in serialized
    assert "secret-outing" not in serialized


def test_outing_score_promotes_outdoor_terms_and_demotes_screenshots():
    outdoor_score, outdoor_reasons = score_outing_likelihood(
        "駅前のレストランと屋外の写真",
        media_type="image",
        mime_type="image/jpeg",
        has_annotation=True,
    )
    screen_score, screen_reasons = score_outing_likelihood(
        "スクリーンショット 画面 文書",
        media_type="image",
        mime_type="image/png",
        has_annotation=True,
    )

    assert outdoor_score > screen_score
    assert outdoor_score >= 0.45
    assert "outing_annotation_keyword" in outdoor_reasons
    assert "low_outing_document_or_screenshot_keyword" in screen_reasons


def test_temporal_answer_clusters_days_and_adds_line_note_support(tmp_path):
    db_path = tmp_path / "temporal.sqlite3"
    storage = initialize_database(db_path)
    try:
        media_id = _insert_photo(
            storage,
            taken_at="2025-12-03T10:00:00",
            annotation_text="駅とレストランで外出している写真",
            path="/private/hidden.jpg",
            metadata={"gps": {"present": True}},
        )
        weak_id = _insert_photo(
            storage,
            taken_at="2025-12-04T10:00:00",
            annotation_text="スクリーンショット 画面",
        )
        line_id = _insert_line(storage, text="RAW LINE SHOULD NOT LEAK")
        note_id = _insert_note(storage, body="RAW NOTE SHOULD NOT LEAK")
    finally:
        storage.close()

    result = answer_temporal_event_query(
        "2025年12月で出かけたのはいつ？",
        db_path=db_path,
        today=date(2026, 5, 26),
    )
    assert result is not None
    payload = result.to_dict(show_answer=True)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert result.ok is True
    assert payload["answer"]["dates"][0]["date"] == "2025-12-03"
    assert f"media_items:{media_id}" in payload["answer"]["evidence_references"]
    assert f"line_messages:{line_id}" in payload["answer"]["evidence_references"]
    assert f"notes:{note_id}" in payload["answer"]["evidence_references"]
    assert f"media_items:{weak_id}" not in payload["answer"]["evidence_references"]
    weak = next(item for item in payload["evidence"] if item["evidence_id"] == f"media_items:{weak_id}")
    assert weak["should_use"] is False
    assert weak["used_by_answer"] is False
    assert weak["evidence_role"] == "rejected"
    assert "RAW LINE SHOULD NOT LEAK" not in serialized
    assert "RAW NOTE SHOULD NOT LEAK" not in serialized
    assert "/private/hidden.jpg" not in serialized


def test_temporal_insufficient_evidence_returns_unknown(tmp_path):
    db_path = tmp_path / "temporal.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_photo(
            storage,
            taken_at="2025-12-04T10:00:00",
            annotation_text="スクリーンショット 画面 文書",
        )
    finally:
        storage.close()

    result = answer_temporal_event_query("2025年12月で出かけたのはいつ？", db_path=db_path)

    assert result is not None
    assert result.ok is False
    assert result.answer.confidence == 0.0
    assert result.answer.evidence_references == ()
    assert result.diagnostics["weak_evidence_separated"] is True


def test_temporal_diagnostics_recommend_timestamp_backfill_when_taken_at_missing(tmp_path):
    db_path = tmp_path / "temporal.sqlite3"
    storage = initialize_database(db_path)
    try:
        source_id = storage.source_items.insert_source(
            source_type="photo",
            source_uri="fixture://missing-taken-at",
            content_sha256="sha-missing-taken-at",
        )
        storage.media_items.insert_media(
            source_item_id=source_id,
            media_type="image",
            file_path="/synthetic/not-printed.jpg",
            sha256="sha-missing-taken-at",
            mime_type="image/jpeg",
        )
    finally:
        storage.close()

    result = answer_temporal_event_query("2025年12月で出かけたのはいつ？", db_path=db_path)

    assert result is not None
    assert result.diagnostics["media_items_with_taken_at_count"] == 0
    assert result.diagnostics["media_items_missing_taken_at_count"] == 1
    assert result.diagnostics["timestamp_backfill_recommended"] is True
    assert result.diagnostics["parsed_date_range"]["start"] == "2025-12-01"


def test_daily_clustering_groups_by_day_and_keeps_weak_candidates_separate(tmp_path):
    db_path = tmp_path / "temporal.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_photo(storage, taken_at="2025-12-03T10:00:00", annotation_text="駅")
        _insert_photo(storage, taken_at="2025-12-03T11:00:00", annotation_text="カフェ")
        _insert_photo(storage, taken_at="2025-12-04T10:00:00", annotation_text="画面 文書")
    finally:
        storage.close()

    photos = search_photos_by_date_range(db_path, start=date(2025, 12, 1), end=date(2026, 1, 1))
    clusters = cluster_photo_candidates_by_day(db_path, photos)

    assert [cluster.date for cluster in clusters] == ["2025-12-03", "2025-12-04"]
    assert clusters[0].photo_count == 2
    assert clusters[0].confidence > clusters[1].confidence


def test_chat_console_temporal_payload_separates_used_candidate_rejected_evidence(
    temp_config_factory,
    tmp_path,
):
    db_path = tmp_path / "temporal.sqlite3"
    storage = initialize_database(db_path)
    try:
        used_id = _insert_photo(
            storage,
            taken_at="2025-12-03T10:00:00",
            annotation_text="駅とカフェで外出した写真",
        )
        rejected_id = _insert_photo(
            storage,
            taken_at="2025-12-04T10:00:00",
            annotation_text="スクリーンショット",
        )
    finally:
        storage.close()

    payload = run_chat_console_query(
        ChatConsoleOptions(
            config_dir=temp_config_factory(),
            db_path=db_path,
            question="2025年12月で出かけたのはいつ？",
            mode="retrieval-only",
        ),
    )

    assert payload["trace"]["temporal_event"] is True
    assert payload["temporal_event"]["query"]["query_type"] == "temporal_event_search"
    used = next(item for item in payload["evidence"] if item["evidence_id"] == f"media_items:{used_id}")
    rejected = next(item for item in payload["evidence"] if item["evidence_id"] == f"media_items:{rejected_id}")
    assert used["evidence_role"] == "used"
    assert used["used_by_answer"] is True
    assert rejected["evidence_role"] == "rejected"
    assert rejected["used_by_answer"] is False


def test_pma_query_temporal_output_is_privacy_safe(capsys, tmp_path):
    db_path = tmp_path / "temporal.sqlite3"
    storage = initialize_database(db_path)
    try:
        media_id = _insert_photo(
            storage,
            taken_at="2025-12-03T10:00:00",
            annotation_text="駅とレストランで外出",
            path="/private/cli-secret.jpg",
        )
    finally:
        storage.close()

    exit_code = main(
        [
            "query",
            "2025年12月で出かけたのはいつ？",
            "--db",
            str(db_path),
        ],
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "temporal_event_search" in output
    assert f"media_items:{media_id}" in output
    assert "/private/cli-secret.jpg" not in output
    assert "駅とレストラン" not in output
