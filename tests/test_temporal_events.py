import json
from datetime import date

from private_memory_agent.api.console import ChatConsoleOptions, run_chat_console_query
from private_memory_agent.cli import main
from private_memory_agent.storage import initialize_database
from private_memory_agent.temporal import (
    DailyEventCluster,
    DeterministicEventIntentPlanner,
    EventIntentPlan,
    TemporalAnswer,
    TemporalDateRange,
    TemporalEventQuery,
    TemporalEventResult,
    TemporalEvidenceItem,
    answer_temporal_event_query,
    cluster_photo_candidates_by_day,
    parse_temporal_event_query,
    score_outing_likelihood,
    search_photos_by_date_range,
)


class _FakeDiningPlanner:
    def plan(self, question, date_range):
        return EventIntentPlan(
            query_type="temporal_event_search",
            date_range=date_range,
            event_type="dining_out",
            event_description="synthetic dining plan",
            visual_signals=("料理", "レストラン", "テーブル"),
            textual_signals=("ご飯", "レストラン", "集合"),
            source_priorities=("photos", "line", "notes"),
            positive_evidence_criteria=("food or restaurant signal",),
            weak_evidence_criteria=("generic outing only",),
            negative_evidence_criteria=("スクリーンショット",),
            repair_queries=("ご飯 レストラン",),
            fallback_used=False,
            planner="fake",
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
    assert parsed.date_range.source == "deterministic"
    assert parsed.date_range.expression == "2025年12月"
    assert parsed.to_dict()["parsed_temporal_expression"] == "2025年12月"

    last_year = parse_temporal_event_query("去年12月に外出した日は？", today=date(2026, 5, 26))
    assert last_year is not None
    assert last_year.date_range.start.isoformat() == "2025-12-01"

    previous_month = parse_temporal_event_query("先月どこか行った日は？", today=date(2026, 5, 26))
    assert previous_month is not None
    assert previous_month.date_range.start.isoformat() == "2026-04-01"

    summer = parse_temporal_event_query("2025年夏で出かけたのはいつ？", today=date(2026, 5, 26))
    assert summer is not None
    assert summer.date_range.start.isoformat() == "2025-06-01"
    assert summer.date_range.end.isoformat() == "2025-09-01"
    assert summer.date_range.expression == "2025年夏"

    full_year = parse_temporal_event_query("2025年に出かけた日は？", today=date(2026, 5, 26))
    assert full_year is not None
    assert full_year.date_range.start.isoformat() == "2025-01-01"
    assert full_year.date_range.end.isoformat() == "2026-01-01"

    month_range = parse_temporal_event_query(
        "2025年10月から12月で出かけたのはいつ？",
        today=date(2026, 5, 26),
    )
    assert month_range is not None
    assert month_range.date_range.start.isoformat() == "2025-10-01"
    assert month_range.date_range.end.isoformat() == "2026-01-01"
    assert month_range.date_range.confidence == 0.98
    assert month_range.to_dict()["date_range"]["parse_warnings"] == []

    tilde_range = parse_temporal_event_query(
        "2025年10月〜12月で外出した日は？",
        today=date(2026, 5, 26),
    )
    assert tilde_range is not None
    assert tilde_range.date_range.start.isoformat() == "2025-10-01"
    assert tilde_range.date_range.end.isoformat() == "2026-01-01"

    explicit_end_year = parse_temporal_event_query(
        "2025年10月から2025年12月までで出かけたのはいつ？",
        today=date(2026, 5, 26),
    )
    assert explicit_end_year is not None
    assert explicit_end_year.date_range.start.isoformat() == "2025-10-01"
    assert explicit_end_year.date_range.end.isoformat() == "2026-01-01"


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
    assert payload["query"]["date_range_source"] == "deterministic"
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
    assert payload["diagnostics"]["photo_candidates_count"] == 2
    assert payload["diagnostics"]["annotated_photo_candidates_count"] == 2
    assert payload["diagnostics"]["unannotated_photo_candidates_count"] == 0
    assert payload["diagnostics"]["date_range_query_column"] == "taken_at"
    assert payload["diagnostics"]["date_range_query_status"] == "ok"
    assert payload["diagnostics"]["nearby_month_counts"]["current_month_photo_count"] == 2


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
    assert result.diagnostics["parsed_date_range_start"] == "2025-12-01"
    assert result.diagnostics["parsed_date_range_end"] == "2026-01-01"
    assert result.diagnostics["date_range_query_status"] == "missing_taken_at"
    assert result.diagnostics["photo_candidates_count"] == 0


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
    assert payload["trace"]["temporal_diagnostics"]["parsed_date_range_start"] == "2025-12-01"
    assert payload["trace"]["temporal_diagnostics"]["photo_candidates_count"] == 2
    assert payload["temporal_event"]["query"]["query_type"] == "temporal_event_search"
    assert payload["evidence_display"]["candidate_dates"]
    assert payload["evidence_display"]["candidate_dates"][0]["supporting_photos"]
    assert payload["evidence_display"]["candidate_dates"][0]["reason_labels"]
    assert payload["evidence_display"]["candidate_dates"][0]["photos"]
    used = next(item for item in payload["evidence"] if item["evidence_id"] == f"media_items:{used_id}")
    rejected = next(item for item in payload["evidence"] if item["evidence_id"] == f"media_items:{rejected_id}")
    assert used["evidence_role"] == "used"
    assert used["used_by_answer"] is True
    assert rejected["evidence_role"] == "rejected"
    assert rejected["used_by_answer"] is False
    rejected_display = [
        item
        for date_item in payload["evidence_display"]["candidate_dates"]
        for item in date_item["rejected_evidence"]
    ]
    assert any(item["evidence_id"] == f"media_items:{rejected_id}" for item in rejected_display)


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
            "--temporal-diagnostics",
        ],
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "temporal_event_search" in output
    assert f"media_items:{media_id}" in output
    assert "/private/cli-secret.jpg" not in output
    assert "駅とレストラン" not in output


def test_temporal_line_notes_fallback_finds_support_when_photos_missing(tmp_path):
    db_path = tmp_path / "temporal.sqlite3"
    storage = initialize_database(db_path)
    try:
        line_id = _insert_line(
            storage,
            sent_at="2025-12-12T18:00:00",
            text="PRIVATE LINE 外出 駅 食事",
        )
        note_id = _insert_note(
            storage,
            updated_at="2025-12-12T20:00:00",
            body="PRIVATE NOTE 旅行 予定",
        )
    finally:
        storage.close()

    result = answer_temporal_event_query("2025年12月で出かけたのはいつ？", db_path=db_path)
    payload = result.to_dict(show_answer=True)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert result.ok is True
    assert payload["answer"]["dates"][0]["date"] == "2025-12-12"
    assert f"line_messages:{line_id}" in payload["answer"]["evidence_references"]
    assert f"notes:{note_id}" in payload["answer"]["evidence_references"]
    assert payload["answer"]["confidence"] < 0.45
    assert payload["diagnostics"]["photo_candidates_count"] == 0
    assert payload["diagnostics"]["line_date_support_count"] == 1
    assert payload["diagnostics"]["notes_date_support_count"] == 1
    assert payload["diagnostics"]["fallback_sources_used"] == ["line", "notes"]
    fallback_evidence = next(item for item in payload["evidence"] if item["evidence_id"] == f"line_messages:{line_id}")
    assert fallback_evidence["specificity"] == "weak"
    assert fallback_evidence["should_use"] is True
    assert "写真候補は見つかりませんでした" in payload["answer"]["conclusion"]
    assert "PRIVATE LINE" not in serialized
    assert "PRIVATE NOTE" not in serialized


def test_temporal_line_notes_fallback_terms_are_configurable(tmp_path):
    db_path = tmp_path / "temporal.sqlite3"
    storage = initialize_database(db_path)
    try:
        line_id = _insert_line(
            storage,
            sent_at="2025-12-12T18:00:00",
            text="PRIVATE LINE 美術館",
        )
    finally:
        storage.close()

    default_result = answer_temporal_event_query(
        "2025年12月で出かけたのはいつ？",
        db_path=db_path,
    )
    custom_result = answer_temporal_event_query(
        "2025年12月で出かけたのはいつ？",
        db_path=db_path,
        fallback_terms=("美術館",),
    )

    assert default_result is not None
    assert default_result.ok is False
    assert custom_result is not None
    payload = custom_result.to_dict(show_answer=True)
    serialized = json.dumps(payload, ensure_ascii=False)
    assert custom_result.ok is True
    assert f"line_messages:{line_id}" in payload["answer"]["evidence_references"]
    assert "PRIVATE LINE" not in serialized


def test_temporal_all_sources_missing_reports_clear_unknown(tmp_path):
    db_path = tmp_path / "temporal.sqlite3"
    initialize_database(db_path).close()

    result = answer_temporal_event_query("2025年12月で出かけたのはいつ？", db_path=db_path)
    payload = result.to_dict(show_answer=True)

    assert result.ok is False
    assert payload["answer"]["confidence"] == 0.0
    assert payload["answer"]["evidence_references"] == []
    assert payload["diagnostics"]["photo_candidates_count"] == 0
    assert payload["diagnostics"]["line_date_support_count"] == 0
    assert payload["diagnostics"]["notes_date_support_count"] == 0
    assert "写真、LINE、ノート" in payload["answer"]["unknowns"][0]


def test_broad_temporal_range_is_chunked_and_candidate_dates_are_pruned(tmp_path):
    db_path = tmp_path / "temporal.sqlite3"
    storage = initialize_database(db_path)
    try:
        for month in (6, 7, 8):
            for day in (3, 10, 17, 24):
                _insert_photo(
                    storage,
                    taken_at=f"2025-{month:02d}-{day:02d}T10:00:00",
                    annotation_text="駅とカフェで外出した写真",
                )
    finally:
        storage.close()

    result = answer_temporal_event_query(
        "2025年夏で出かけたのはいつ？",
        db_path=db_path,
        top_candidate_dates=4,
        top_evidence_per_date=1,
    )
    assert result is not None
    payload = result.to_dict(show_answer=True)

    assert result.ok is True
    assert payload["diagnostics"]["chunking_enabled"] is True
    assert payload["diagnostics"]["chunk_count"] == 3
    assert payload["diagnostics"]["chunk_size"] == "month"
    assert payload["diagnostics"]["candidates_before_pruning"] == 12
    assert payload["diagnostics"]["candidates_after_pruning"] == 4
    assert payload["diagnostics"]["top_candidate_dates"] == 4
    assert payload["diagnostics"]["top_evidence_per_date"] == 1
    assert payload["diagnostics"]["evidence_sent_count"] <= 4
    assert len(payload["candidate_dates"]) == 4
    assert len(payload["answer"]["dates"]) == 4


def test_chat_console_broad_temporal_query_returns_candidates_without_model_error(
    temp_config_factory,
    tmp_path,
):
    db_path = tmp_path / "temporal.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_photo(
            storage,
            taken_at="2025-07-12T10:00:00",
            annotation_text="駅とレストランで外出",
            path="/private/broad-secret.jpg",
        )
    finally:
        storage.close()

    payload = run_chat_console_query(
        ChatConsoleOptions(
            config_dir=temp_config_factory(),
            db_path=db_path,
            question="2025年夏で出かけたのはいつ？",
            mode="real-model",
        ),
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["trace"]["temporal_event"] is True
    assert payload["answer"]["answer_succeeded"] is True
    assert payload["answer"]["error_class"] is None
    assert payload["trace"]["temporal_diagnostics"]["chunking_enabled"] is True
    assert payload["evidence_display"]["candidate_dates"]
    assert "/private/broad-secret.jpg" not in serialized
    assert "駅とレストラン" not in serialized


def test_temporal_month_range_diagnostics_show_coverage_and_pruning_warning(tmp_path):
    db_path = tmp_path / "temporal.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_photo(
            storage,
            taken_at="2025-10-11T10:00:00",
            annotation_text="駅とカフェで外出した写真",
        )
        _insert_photo(
            storage,
            taken_at="2025-11-12T10:00:00",
            annotation_text="駅とカフェで外出した写真",
        )
        _insert_photo(
            storage,
            taken_at="2025-12-13T10:00:00",
            annotation_text="駅とカフェで外出した写真",
        )
    finally:
        storage.close()

    result = answer_temporal_event_query(
        "2025年10月から12月で出かけたのはいつ？",
        db_path=db_path,
        top_candidate_dates=1,
        top_evidence_per_date=1,
    )
    assert result is not None
    payload = result.to_dict(show_answer=True)

    assert payload["diagnostics"]["parsed_date_range_start"] == "2025-10-01"
    assert payload["diagnostics"]["parsed_date_range_end"] == "2026-01-01"
    assert payload["diagnostics"]["date_range_confidence"] == 0.98
    assert payload["diagnostics"]["date_range_parse_warnings"] == []
    assert payload["diagnostics"]["months_covered"] == ["2025-10", "2025-11", "2025-12"]
    assert payload["diagnostics"]["photo_count_by_month"] == {
        "2025-10": 1,
        "2025-11": 1,
        "2025-12": 1,
    }
    assert payload["diagnostics"]["candidate_date_count_by_month"] == {
        "2025-10": 1,
        "2025-11": 1,
        "2025-12": 1,
    }
    assert payload["diagnostics"]["final_candidate_date_count_by_month"] == {
        "2025-10": 1,
        "2025-11": 0,
        "2025-12": 0,
    }
    assert payload["diagnostics"]["pruned_months"] == ["2025-11", "2025-12"]
    assert payload["diagnostics"]["top_candidate_date_limit"] == 1
    assert any("Final candidates only cover 2025-10" in warning for warning in payload["warnings"])


def test_chat_console_month_range_response_exposes_safe_month_coverage(
    temp_config_factory,
    tmp_path,
):
    db_path = tmp_path / "temporal.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_photo(
            storage,
            taken_at="2025-10-11T10:00:00",
            annotation_text="PRIVATE station cafe outing",
            path="/private/month-range-secret.jpg",
        )
    finally:
        storage.close()

    payload = run_chat_console_query(
        ChatConsoleOptions(
            config_dir=temp_config_factory(),
            db_path=db_path,
            question="2025年10月から12月で出かけたのはいつ？",
            mode="retrieval-only",
            temporal_top_candidate_dates=1,
        ),
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["trace"]["temporal_diagnostics"]["parsed_date_range_start"] == "2025-10-01"
    assert payload["trace"]["temporal_diagnostics"]["parsed_date_range_end"] == "2026-01-01"
    assert payload["trace"]["temporal_diagnostics"]["months_covered"] == [
        "2025-10",
        "2025-11",
        "2025-12",
    ]
    assert payload["trace"]["temporal_diagnostics"]["photo_count_by_month"]["2025-10"] == 1
    assert "/private/month-range-secret.jpg" not in serialized
    assert "PRIVATE station cafe outing" not in serialized


def test_temporal_dining_out_intent_finds_food_photo_and_demotes_generic_outdoor(
    tmp_path,
):
    db_path = tmp_path / "temporal.sqlite3"
    storage = initialize_database(db_path)
    try:
        food_id = _insert_photo(
            storage,
            taken_at="2025-12-05T19:00:00",
            annotation_text="料理 レストラン テーブル",
            path="/private/food-secret.jpg",
        )
        generic_id = _insert_photo(
            storage,
            taken_at="2025-12-06T14:00:00",
            annotation_text="屋外 公園",
            path="/private/generic-secret.jpg",
        )
        line_id = _insert_line(
            storage,
            sent_at="2025-12-07T18:00:00",
            text="PRIVATE LINE レストラン 集合 ご飯",
        )
    finally:
        storage.close()

    result = answer_temporal_event_query(
        "2025年12月でご飯を食べに行っているのはいつ？",
        db_path=db_path,
        event_planner=_FakeDiningPlanner(),
    )
    assert result is not None
    payload = result.to_dict(show_answer=True)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["query"]["event_type"] == "dining_out"
    assert payload["diagnostics"]["event_type"] == "dining_out"
    assert payload["diagnostics"]["visual_signal_count"] == 3
    assert payload["diagnostics"]["textual_signal_count"] == 3
    dates = {item["date"]: item for item in payload["candidate_dates"]}
    assert "2025-12-05" in dates
    assert dates["2025-12-05"]["event_score"] >= 0.45
    assert dates["2025-12-05"]["matched_visual_signal_count"] >= 2
    assert f"media_items:{food_id}" in payload["answer"]["evidence_references"]
    assert "2025-12-07" in dates
    assert f"line_messages:{line_id}" in dates["2025-12-07"]["support_evidence_ids"]
    generic_evidence = next(
        item for item in payload["evidence"] if item["evidence_id"] == f"media_items:{generic_id}"
    )
    assert generic_evidence["used_by_answer"] is False
    assert generic_evidence["evidence_role"] in {"candidate", "rejected"}
    assert "/private/food-secret.jpg" not in serialized
    assert "PRIVATE LINE" not in serialized


def test_deterministic_event_planner_infers_dining_out_open_vocabulary():
    parsed = parse_temporal_event_query("2025年12月でご飯を食べに行っているのはいつ？")
    assert parsed is not None
    plan = DeterministicEventIntentPlanner().plan(
        "2025年12月でご飯を食べに行っているのはいつ？",
        parsed.date_range,
    )
    assert plan.event_type == "dining_out"
    assert "レストラン" in plan.visual_signals
    assert "ご飯" in plan.textual_signals


def test_chat_console_dining_query_exposes_event_intent_diagnostics(
    temp_config_factory,
    tmp_path,
):
    db_path = tmp_path / "temporal.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_photo(
            storage,
            taken_at="2025-12-05T19:00:00",
            annotation_text="料理 レストラン テーブル",
            path="/private/ui-dining-secret.jpg",
        )
    finally:
        storage.close()

    payload = run_chat_console_query(
        ChatConsoleOptions(
            config_dir=temp_config_factory(),
            db_path=db_path,
            question="2025年12月でご飯を食べに行っているのはいつ？",
            mode="retrieval-only",
        ),
    )
    serialized = json.dumps(payload, ensure_ascii=False)
    diagnostics = payload["trace"]["temporal_diagnostics"]

    assert diagnostics["event_type"] == "dining_out"
    assert diagnostics["visual_signal_count"] > 0
    assert diagnostics["textual_signal_count"] > 0
    assert diagnostics["candidate_date_count"] > 0
    assert diagnostics["event_score_by_date"]
    assert payload["evidence_display"]["candidate_dates"][0]["event_score"] >= 0.45
    trace_events = payload["trace_events"]
    actor_names = {event["actor_name"] for event in trace_events}
    statuses = {event["status"] for event in trace_events}
    assert "DateRangeParserTool" in actor_names
    assert "DeterministicEventIntentPlanner" in actor_names
    assert "PhotoDateSearchTool" in actor_names
    assert "Qwen3-VL" in actor_names
    assert "LineNotesDateSearchTool" in actor_names
    assert "AnswerValidator" in actor_names
    qwen_event = next(event for event in trace_events if event["actor_name"] == "Qwen3-VL")
    assert qwen_event["invocation_type"] == "cached_artifact"
    assert qwen_event["artifact_type"] == "photo_annotation"
    assert "fallback_used" in statuses
    assert payload["model_usage_summary"]["Qwen3-VL"]["cached_artifacts"] >= 1
    assert "/private/ui-dining-secret.jpg" not in serialized
    assert "料理 レストラン" not in serialized


def test_open_ended_ramen_query_uses_all_available_memory_and_extracts_dates(tmp_path):
    db_path = tmp_path / "temporal.sqlite3"
    storage = initialize_database(db_path)
    try:
        photo_id = _insert_photo(
            storage,
            taken_at="2025-01-12T19:00:00",
            annotation_text="ラーメン 丼 スープ 箸",
            path="/private/ramen-photo-secret.jpg",
        )
        line_id = _insert_line(
            storage,
            sent_at="2025-02-03T18:15:00",
            text="PRIVATE LINE ラーメン 食べに行った",
        )
    finally:
        storage.close()

    parsed = parse_temporal_event_query("ラーメンを食べに行っているのはいつ？", today=date(2026, 5, 26))
    assert parsed is not None
    assert parsed.query_type == "temporal_event_search"
    assert parsed.date_range.status == "unspecified"
    assert parsed.date_range.scope_strategy == "all_available_memory"

    result = answer_temporal_event_query(
        "ラーメンを食べに行っているのはいつ？",
        db_path=db_path,
        today=date(2026, 5, 26),
    )
    assert result is not None
    payload = result.to_dict(show_answer=True)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["query"]["date_range_status"] == "unspecified"
    assert payload["query"]["date_scope_strategy"] == "all_available_memory"
    assert payload["diagnostics"]["open_ended_temporal_query"] is True
    assert payload["diagnostics"]["date_scope_strategy"] == "all_available_memory"
    assert payload["diagnostics"]["inferred_search_range_start"] == "2025-01-12"
    assert payload["diagnostics"]["inferred_search_range_end"] == "2025-02-04"
    assert payload["diagnostics"]["event_type"] == "dining_out"
    assert payload["diagnostics"]["event_subtype"] == "ramen"
    assert payload["diagnostics"]["chunks_scanned"] >= 1
    assert payload["diagnostics"]["dated_evidence_count"] >= 1
    assert payload["diagnostics"]["undated_evidence_count"] == 0
    dates = {item["date"]: item for item in payload["candidate_dates"]}
    assert "2025-01-12" in dates
    assert "2025-02-03" in dates
    assert payload["diagnostics"]["candidate_date_count"] >= 2
    assert f"media_items:{photo_id}" in payload["answer"]["evidence_references"]
    assert f"line_messages:{line_id}" in dates["2025-02-03"]["support_evidence_ids"]
    assert "/private/ramen-photo-secret.jpg" not in serialized
    assert "PRIVATE LINE" not in serialized


def test_open_ended_event_query_with_only_undated_evidence_returns_structured_unknown(tmp_path):
    db_path = tmp_path / "temporal.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_photo(
            storage,
            taken_at=None,
            annotation_text="ラーメン 丼 スープ",
            path="/private/undated-ramen-secret.jpg",
        )
    finally:
        storage.close()

    result = answer_temporal_event_query(
        "ラーメンを食べに行っているのはいつ？",
        db_path=db_path,
        today=date(2026, 5, 26),
    )
    assert result is not None
    payload = result.to_dict(show_answer=True)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["answer"]["answer_succeeded"] is True
    assert payload["answer"]["answer_state"] == "unknown"
    assert payload["answer"]["error_class"] is None
    assert payload["candidate_dates"] == []
    assert payload["diagnostics"]["open_ended_temporal_query"] is True
    assert payload["diagnostics"]["undated_evidence_count"] == 1
    assert payload["diagnostics"]["dated_evidence_count"] == 0
    assert payload["diagnostics"]["candidate_date_count"] == 0
    assert any("日時メタデータ" in unknown for unknown in payload["answer"]["unknowns"])
    assert "/private/undated-ramen-secret.jpg" not in serialized
    assert "ラーメン 丼" not in serialized


def test_chat_console_open_ended_ramen_query_does_not_return_model_runtime_error(
    temp_config_factory,
    tmp_path,
):
    db_path = tmp_path / "temporal.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_photo(
            storage,
            taken_at="2025-03-04T12:00:00",
            annotation_text="ラーメン 丼 スープ",
            path="/private/ui-open-ramen-secret.jpg",
        )
    finally:
        storage.close()

    payload = run_chat_console_query(
        ChatConsoleOptions(
            config_dir=temp_config_factory(),
            db_path=db_path,
            question="ラーメンを食べに行っているのはいつ？",
            mode="real-model",
            leader_plan=False,
            show_answer=True,
        ),
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["mode"] == "real-model"
    assert payload["answer_succeeded"] is True
    assert payload["answer_synthesis_succeeded"] is True
    assert payload["error_class"] is None
    assert payload["answer_error_class"] is None
    assert payload["candidate_date_count"] >= 1
    assert payload["trace"]["temporal_diagnostics"]["open_ended_temporal_query"] is True
    assert payload["trace"]["temporal_diagnostics"]["event_subtype"] == "ramen"
    assert "/private/ui-open-ramen-secret.jpg" not in serialized
    assert "ラーメン 丼" not in serialized


def test_chat_console_real_model_temporal_answer_failure_preserves_candidates(
    monkeypatch,
    temp_config_factory,
    tmp_path,
):
    date_range = TemporalDateRange(
        start=date(2025, 12, 1),
        end=date(2026, 1, 1),
        label="2025-12",
        expression="2025年12月",
    )
    query = TemporalEventQuery(
        query_type="temporal_event_search",
        date_range=date_range,
        event_type="dining_out",
        preferred_sources=("photos", "line", "notes"),
        primary_tool="photo_date_range_search",
    )
    cluster = DailyEventCluster(
        date="2025-12-05",
        photo_count=2,
        annotated_photo_count=2,
        outing_score=0.8,
        confidence=0.72,
        top_evidence_ids=("media_items:1",),
        candidate_evidence_ids=("media_items:1", "line_messages:1"),
        rejected_evidence_ids=(),
        line_support_count=1,
        notes_support_count=0,
        support_evidence_ids=("line_messages:1",),
        reason="synthetic dining support",
        event_score=0.75,
        matched_visual_signals=("料理",),
        matched_textual_signals=("ご飯",),
    )
    result = TemporalEventResult(
        ok=False,
        query=query,
        answer=TemporalAnswer(
            answer_succeeded=False,
            conclusion="",
            confidence=0.0,
            dates=(),
            evidence_references=(),
            used_sources=(),
            unknowns=("synthetic model failure",),
        ),
        evidence=(
            TemporalEvidenceItem(
                evidence_id="media_items:1",
                source_type="photos",
                should_use=True,
                evidence_role="candidate",
                specificity="specific",
                relevance_score=0.75,
                reason_category="temporal_event_specific_photo_match",
                occurred_at="2025-12-05T19:00:00",
            ),
            TemporalEvidenceItem(
                evidence_id="line_messages:1",
                source_type="line",
                should_use=True,
                evidence_role="candidate",
                specificity="specific",
                relevance_score=0.65,
                reason_category="same_day_line_support",
                occurred_at="2025-12-05T18:30:00",
            ),
        ),
        candidate_dates=(cluster,),
        diagnostics={
            "candidate_date_count": 1,
            "event_type": "dining_out",
            "parsed_date_range_start": "2025-12-01",
            "parsed_date_range_end": "2026-01-01",
        },
        warnings=("synthetic answer generation failed",),
    )

    def fake_answer_temporal_event_query(*args, **kwargs):
        return result

    monkeypatch.setattr(
        "private_memory_agent.api.console.answer_temporal_event_query",
        fake_answer_temporal_event_query,
    )

    payload = run_chat_console_query(
        ChatConsoleOptions(
            config_dir=temp_config_factory(),
            db_path=tmp_path / "temporal.sqlite3",
            question="2025年12月でご飯を食べに行っているのはいつ？",
            mode="real-model",
            leader_plan=False,
        ),
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["mode"] == "real-model"
    assert payload["evidence_builder_succeeded"] is True
    assert payload["answer_synthesis_succeeded"] is False
    assert payload["answer_succeeded"] is False
    assert payload["failure_stage"] == "answer_generation"
    assert payload["failure_actor"] == "DeepSeek Leader"
    assert payload["candidate_date_count"] == 1
    assert payload["evidence_count"] == 2
    assert payload["evidence_display"]["candidate_dates"]
    assert payload["temporal_event"]["candidate_dates"]
    assert payload["trace_events"]
    assert "PRIVATE" not in serialized
