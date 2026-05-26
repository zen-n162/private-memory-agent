import json
from pathlib import Path

import pytest
from PIL import Image

from private_memory_agent.api.evidence_view import (
    EvidenceDisplayOptions,
    EvidenceThumbnailError,
    build_evidence_display_payload,
    create_media_thumbnail,
)
from private_memory_agent.storage import initialize_database


def _synthetic_image(path: Path) -> None:
    Image.new("RGB", (640, 480), color=(32, 96, 140)).save(path, format="JPEG")


def _insert_media(storage, image_path: Path, *, taken_at="2025-12-03T10:00:00") -> int:
    source_id = storage.source_items.insert_source(
        source_type="photo",
        source_uri=f"fixture://{image_path.name}",
        content_sha256=f"sha-{image_path.name}",
    )
    media_id = storage.media_items.insert_media(
        source_item_id=source_id,
        media_type="image",
        file_path=str(image_path),
        sha256=f"sha-{image_path.name}",
        mime_type="image/jpeg",
        width=640,
        height=480,
        taken_at=taken_at,
    )
    storage.media_annotations.insert(
        {
            "media_item_id": media_id,
            "annotation_type": "vision",
            "source": "model",
            "value_text": "SECRET CAPTION 駅とカフェ",
            "model_id": "fake-vl",
            "confidence": 0.9,
        },
    )
    return media_id


def _insert_line(storage) -> int:
    return storage.line_messages.insert_message(
        source_item_id=None,
        conversation_id="private-room",
        message_id="line-evidence-view",
        sender_id="PRIVATE_SPEAKER",
        sent_at="2025-12-03T18:00:00",
        message_type="text",
        body_text="PRIVATE LINE BODY " + ("外出 " * 20),
    )


def _insert_note(storage) -> int:
    return storage.notes.insert_note(
        source_item_id=None,
        note_id="note-evidence-view",
        title="PRIVATE NOTE TITLE",
        body_text="PRIVATE NOTE BODY " + ("予定 " * 20),
        created_at_source="2025-12-03T19:00:00",
        updated_at_source="2025-12-03T19:30:00",
    )


def test_evidence_display_payload_groups_candidate_date_without_default_snippets(tmp_path):
    db_path = tmp_path / "evidence.sqlite3"
    image_path = tmp_path / "private_photo.jpg"
    _synthetic_image(image_path)
    storage = initialize_database(db_path)
    try:
        media_id = _insert_media(storage, image_path)
        line_id = _insert_line(storage)
        note_id = _insert_note(storage)
    finally:
        storage.close()

    media_evidence_id = f"media_items:{media_id}"
    line_evidence_id = f"line_messages:{line_id}"
    note_evidence_id = f"notes:{note_id}"
    payload = build_evidence_display_payload(
        db_path,
        evidence=[
            {"evidence_id": media_evidence_id, "source_type": "photos", "evidence_role": "used", "used_by_answer": True},
            {"evidence_id": line_evidence_id, "source_type": "line", "evidence_role": "used", "used_by_answer": True},
            {"evidence_id": note_evidence_id, "source_type": "notes", "evidence_role": "candidate", "used_by_answer": False},
        ],
        answer_evidence_references=(media_evidence_id, line_evidence_id),
        candidate_dates=[
            {
                "date": "2025-12-03",
                "confidence": 0.82,
                "reason": "synthetic outing support",
                "photo_count": 1,
                "annotated_photo_count": 1,
                "line_support_count": 1,
                "notes_support_count": 1,
                "top_evidence_ids": [media_evidence_id],
                "support_evidence_ids": [line_evidence_id],
                "candidate_evidence_ids": [note_evidence_id],
                "rejected_evidence_ids": [],
            },
        ],
        options=EvidenceDisplayOptions(show_snippets=False, show_photo_thumbnails=True),
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["candidate_dates"][0]["supporting_photos"][0]["thumbnail_url"].endswith(
        f"/{media_id}/thumbnail",
    )
    assert payload["candidate_dates"][0]["supporting_line_snippets"][0]["snippet_hidden"] is True
    assert payload["candidate_dates"][0]["candidate_evidence"][0]["title_hidden"] is True
    assert payload["evidence_reference_groups"]["photos"] == [media_evidence_id]
    assert "PRIVATE LINE BODY" not in serialized
    assert "PRIVATE NOTE BODY" not in serialized
    assert "PRIVATE NOTE TITLE" not in serialized
    assert str(image_path) not in serialized


def test_evidence_display_payload_shows_truncated_snippets_only_when_requested(tmp_path):
    db_path = tmp_path / "evidence.sqlite3"
    image_path = tmp_path / "photo.jpg"
    _synthetic_image(image_path)
    storage = initialize_database(db_path)
    try:
        media_id = _insert_media(storage, image_path)
        line_id = _insert_line(storage)
        note_id = _insert_note(storage)
    finally:
        storage.close()

    payload = build_evidence_display_payload(
        db_path,
        evidence=[
            {"evidence_id": f"media_items:{media_id}", "source_type": "photos"},
            {"evidence_id": f"line_messages:{line_id}", "source_type": "line"},
            {"evidence_id": f"notes:{note_id}", "source_type": "notes"},
        ],
        options=EvidenceDisplayOptions(show_snippets=True, snippet_chars=40),
    )

    line = payload["groups"]["line"][0]
    note = payload["groups"]["notes"][0]
    photo = payload["groups"]["photos"][0]
    assert line["snippet"].startswith("PRIVATE LINE BODY")
    assert len(line["snippet"]) <= 40
    assert note["title"] == "PRIVATE NOTE TITLE"
    assert len(note["snippet"]) <= 40
    assert photo["annotation_summary"].startswith("SECRET CAPTION")


def test_create_media_thumbnail_validates_indexed_media_id_and_hides_paths(tmp_path):
    db_path = tmp_path / "evidence.sqlite3"
    image_path = tmp_path / "thumb.jpg"
    _synthetic_image(image_path)
    storage = initialize_database(db_path)
    try:
        media_id = _insert_media(storage, image_path)
    finally:
        storage.close()

    content, media_type = create_media_thumbnail(db_path, media_id, max_side=128)

    assert media_type == "image/jpeg"
    assert content.startswith(b"\xff\xd8")
    with pytest.raises(EvidenceThumbnailError) as exc_info:
        create_media_thumbnail(db_path, media_id + 999, max_side=128)
    assert "thumb.jpg" not in str(exc_info.value)
    assert str(tmp_path) not in str(exc_info.value)
