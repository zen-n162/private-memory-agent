import json

from private_memory_agent.cli import main
from private_memory_agent.storage import initialize_database
from private_memory_agent.timeline import EventBuilder, build_events, list_events


def seed_multisource_day(db_path):
    storage = initialize_database(db_path)
    try:
        source_id = storage.source_items.insert_source(
            source_type="photo",
            source_uri="fixture://timeline-photo",
            content_sha256="timeline-photo-sha",
        )
        media_id = storage.media_items.insert_media(
            source_item_id=source_id,
            media_type="image",
            file_path="fixture://timeline-photo.jpg",
            sha256="timeline-photo-sha",
            taken_at="2026-05-24T10:00:00",
            metadata_json=json.dumps(
                {"gps": {"latitude": 35.123456, "longitude": 139.987654}},
                sort_keys=True,
            ),
        )
        storage.media_annotations.insert(
            {
                "media_item_id": media_id,
                "annotation_type": "vision",
                "source": "model",
                "value_text": "synthetic caption",
                "data_json": json.dumps(
                    {"objects": ["ケーキ"], "place": "渋谷"},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "confidence": 0.8,
                "model_id": "fake-vl",
            },
        )
        line_id = storage.line_messages.insert_message(
            source_item_id=None,
            conversation_id="fixture-room",
            message_id="line-timeline-1",
            sender_id="fixture-speaker",
            sent_at="2026-05-24T10:15:00",
            message_type="text",
            body_text="秘密の本文は表示しない。",
        )
        note_id = storage.notes.insert_note(
            source_item_id=None,
            note_id="note-timeline-1",
            title="秘密メモ",
            body_text="秘密のノート本文。",
            created_at_source="2026-05-24T10:45:00",
            updated_at_source="2026-05-24T10:45:00",
        )
        for table, source_id_value in (("line_messages", line_id), ("notes", note_id)):
            storage.text_annotations.insert_text_annotation(
                source_table=table,
                source_id=source_id_value,
                annotation_type="understanding",
                model_id="fake-text",
                summary="synthetic summary",
                entities_json=json.dumps(
                    [
                        {"text": "山田太郎", "type": "person", "confidence": 0.9},
                        {"text": "渋谷", "type": "place", "confidence": 0.9},
                    ],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                topics_json=json.dumps(["誕生日"], ensure_ascii=False),
                dates_json=json.dumps([], ensure_ascii=False),
                action_items_json=json.dumps([], ensure_ascii=False),
                event_hints_json=json.dumps(
                    [{"title": "誕生日の集まり", "date_text": "2026-05-24"}],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                confidence=0.85,
            )
        return {"media_id": media_id, "line_id": line_id, "note_id": note_id}
    finally:
        storage.close()


def test_event_builder_groups_synthetic_multisource_day(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    ids = seed_multisource_day(db_path)

    result = build_events(db_path, timezone="Asia/Tokyo", window_minutes=120)

    assert result.events_created == 1
    assert result.evidence_candidates == 3
    assert result.evidence_links_created == 3

    storage = initialize_database(db_path)
    try:
        event = storage.events.list()[0]
        metadata = json.loads(event["metadata_json"])
        linked = storage.evidence_links.list()
    finally:
        storage.close()

    assert event["event_type"] == "tentative"
    assert event["started_at"] == "2026-05-24T10:00:00+09:00"
    assert event["ended_at"] == "2026-05-24T10:45:00+09:00"
    assert metadata["status"] == "tentative"
    assert metadata["identity_assertions"] is False
    assert set(metadata["evidence_ids"]) == {
        f"media_items:{ids['media_id']}",
        f"line_messages:{ids['line_id']}",
        f"notes:{ids['note_id']}",
    }
    assert "山田太郎" in metadata["participants"]
    assert "渋谷" in metadata["places"]
    assert "誕生日" in metadata["topics"]
    assert len(linked) == 3


def test_event_builder_is_idempotent_by_group_key(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    seed_multisource_day(db_path)

    first = build_events(db_path, timezone="Asia/Tokyo", window_minutes=120)
    second = build_events(db_path, timezone="Asia/Tokyo", window_minutes=120)

    assert first.events_created == 1
    assert second.events_created == 0
    assert second.events_existing == 1
    assert len(list_events(db_path, redact_private=False)) == 1


def test_event_builder_collects_candidates_without_confirming_identity(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    seed_multisource_day(db_path)
    builder = EventBuilder(db_path, timezone="Asia/Tokyo", window_minutes=120)

    storage = initialize_database(db_path)
    try:
        candidates = builder.collect_candidates(storage)
    finally:
        storage.close()

    assert len(candidates) == 3
    assert any("山田太郎" in item.participants for item in candidates)
    assert all(item.source_kind in {"photos", "line", "notes"} for item in candidates)


def test_events_cli_build_and_list_are_privacy_safe(capsys, tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    seed_multisource_day(db_path)

    build_code = main(
        [
            "events",
            "build",
            "--db",
            str(db_path),
            "--timezone",
            "Asia/Tokyo",
            "--window-minutes",
            "120",
        ],
    )
    build_output = capsys.readouterr().out
    assert build_code == 0
    assert "山田太郎" not in build_output
    assert "秘密" not in build_output

    list_code = main(["events", "list", "--db", str(db_path)])
    list_output = capsys.readouterr().out
    payload = json.loads(list_output)

    assert list_code == 0
    assert payload["redacted"] is True
    assert payload["events"][0]["title"] == "[redacted]"
    assert payload["events"][0]["participants"] == ["[redacted]", "[redacted]"]
    assert "山田太郎" not in list_output
    assert "秘密" not in list_output
