import json

from private_memory_agent.api.console import ChatConsoleOptions, run_chat_console_query
from private_memory_agent.api.ui import agent_console_html
from private_memory_agent.storage import initialize_database
from private_memory_agent.visual import (
    DeterministicVisualEvidencePlanner,
    VisualEvidencePlan,
    answer_visual_evidence_query,
    parse_visual_evidence_query,
)


def _insert_photo(
    storage,
    *,
    taken_at="2025-12-03T12:00:00",
    annotation_text=None,
    path="/private/visual-secret-photo.jpg",
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
        width=160,
        height=120,
        taken_at=taken_at,
    )
    if annotation_text is not None:
        storage.media_annotations.insert(
            {
                "media_item_id": media_id,
                "annotation_type": "vision",
                "source": "model",
                "value_text": annotation_text,
                "model_id": "fake-qwen-vl",
                "confidence": 0.8,
            },
        )
    return media_id


def test_visual_query_parser_detects_ramen_photo_gallery_question():
    plan = parse_visual_evidence_query("ラーメンが写っている写真はどれ？")

    assert plan is not None
    assert plan.query_type == "visual_evidence_search"
    assert plan.target_type == "food_object"
    assert "ラーメン" in plan.target_entities
    assert "ラーメン" in plan.visual_signals
    assert plan.output_type == "photo_gallery"


def test_visual_evidence_plan_contains_safe_signal_summary():
    plan = DeterministicVisualEvidencePlanner().plan("カフェの写真を見せて")
    payload = plan.to_dict(show_plan=True)

    assert payload["query_type"] == "visual_evidence_search"
    assert payload["target_description"]
    assert payload["visual_signal_count"] > 0
    assert payload["source_priorities"] == ["photos"]
    assert payload["verification_strategy"] == "cached_annotations_first"


def test_ramen_cached_annotation_produces_used_photo_and_generic_candidate(tmp_path):
    db_path = tmp_path / "visual.sqlite3"
    storage = initialize_database(db_path)
    try:
        used_id = _insert_photo(
            storage,
            annotation_text="ラーメンの丼、スープ、箸がテーブルに置かれている",
            path="/private/ramen-used-secret.jpg",
        )
        generic_id = _insert_photo(
            storage,
            taken_at="2025-12-04T12:00:00",
            annotation_text="料理とメニューがテーブルに置かれている",
            path="/private/generic-food-secret.jpg",
        )
    finally:
        storage.close()

    result = answer_visual_evidence_query(
        "ラーメンが写っている写真はどれ？",
        db_path=db_path,
        verify_with_vision=True,
        max_live_vision_checks=2,
    )
    assert result is not None
    payload = result.to_dict(show_answer=True, show_plan=True)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["query_type"] == "visual_evidence_search"
    assert payload["matching_photo_count"] == 1
    assert payload["answer"]["answer_succeeded"] is True
    assert payload["answer"]["answer_state"] == "visible"
    assert f"media_items:{used_id}" in payload["answer"]["evidence_references"]
    used = next(item for item in payload["evidence"] if item["evidence_id"] == f"media_items:{used_id}")
    generic = next(
        item for item in payload["evidence"] if item["evidence_id"] == f"media_items:{generic_id}"
    )
    assert used["evidence_role"] == "used"
    assert generic["evidence_role"] == "candidate"
    assert generic["used_by_answer"] is False
    assert payload["diagnostics"]["qwen_vl_live_call_count"] <= 2
    assert "/private/ramen-used-secret.jpg" not in serialized
    assert "/private/generic-food-secret.jpg" not in serialized


def test_visual_search_no_matching_photo_returns_structured_unknown(tmp_path):
    db_path = tmp_path / "visual.sqlite3"
    storage = initialize_database(db_path)
    try:
        _insert_photo(
            storage,
            annotation_text="白い壁と書類が写っている",
            path="/private/no-ramen-secret.jpg",
        )
    finally:
        storage.close()

    result = answer_visual_evidence_query("ラーメンが写っている写真はどれ？", db_path=db_path)
    assert result is not None
    payload = result.to_dict(show_answer=True)

    assert payload["answer"]["answer_succeeded"] is True
    assert payload["answer"]["answer_state"] == "unknown"
    assert payload["matching_photo_count"] == 0
    assert payload["answer"]["error_class"] is None


def test_chat_console_visual_search_returns_matching_photo_payload_without_paths(tmp_path):
    db_path = tmp_path / "visual.sqlite3"
    storage = initialize_database(db_path)
    try:
        media_id = _insert_photo(
            storage,
            annotation_text="ラーメン、麺、スープ、箸が写っている",
            path="/private/ui-ramen-photo-secret.jpg",
        )
    finally:
        storage.close()

    payload = run_chat_console_query(
        ChatConsoleOptions(
            question="ラーメンが写っている写真はどれ？",
            db_path=db_path,
            mode="retrieval-only",
            sources=("photos",),
            show_answer=True,
            show_snippets=False,
            show_photo_thumbnails=True,
        ),
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["ok"] is True
    assert payload["query_type"] == "visual_evidence_search"
    assert payload["matching_photo_count"] == 1
    assert payload["candidate_date_count"] == 0
    assert payload["matching_photos"][0]["evidence_id"] == f"media_items:{media_id}"
    assert payload["matching_photos"][0]["thumbnail_url"].endswith(f"/{media_id}/thumbnail")
    assert payload["diagnostics"]["visual_query_detected"] is True
    assert payload["diagnostics"]["qwen_vl_cached_annotations_used_count"] == 1
    assert payload["trace"]["visual_diagnostics"]["target_type"] == "food_object"
    actors = {event["actor_name"] for event in payload["trace_events"]}
    assert "PhotoVisualSearchTool" in actors
    assert "Qwen3-VL" in actors
    assert "VisualEvidenceJudge" in actors
    assert payload["answer_error_class"] is None
    assert "/private/ui-ramen-photo-secret.jpg" not in serialized
    assert "ラーメン、麺、スープ" not in serialized


def test_visual_plan_can_be_fake_leader_supplied(tmp_path):
    class FakePlanner:
        def plan(self, question, *, date_range=None):
            return VisualEvidencePlan(
                query_type="visual_evidence_search",
                target_description="犬が写っている写真",
                target_type="animal",
                target_entities=("犬",),
                visual_signals=("犬", "dog"),
                textual_signals=("犬",),
                fallback_used=False,
                planner="fake",
            )

    db_path = tmp_path / "visual.sqlite3"
    storage = initialize_database(db_path)
    try:
        media_id = _insert_photo(storage, annotation_text="犬が公園にいる", path="/private/dog.jpg")
    finally:
        storage.close()

    result = answer_visual_evidence_query(
        "犬が写っている写真はどれ？",
        db_path=db_path,
        visual_planner=FakePlanner(),
    )
    assert result is not None
    assert result.plan.planner == "fake"
    assert result.answer.evidence_references == (f"media_items:{media_id}",)


def test_ui_contains_visual_gallery_layout_and_controls():
    html = agent_console_html()

    assert 'id="matching-photos-panel"' in html
    assert "renderMatchingPhotos" in html
    assert "Visual evidence search does not require candidate dates." in html
    assert "Visual Evidence Diagnostics" in html
    assert "verify_with_vision" in html
    assert "max_live_vision_checks" in html
