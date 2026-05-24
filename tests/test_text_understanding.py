import json

import pytest

from private_memory_agent.annotation import (
    TextExtractionError,
    annotate_text,
    parse_text_understanding_response,
)
from private_memory_agent.cli import main
from private_memory_agent.runtime import FakeTextUnderstandingClient, TextUnderstandingResponse
from private_memory_agent.storage import initialize_database


def extraction_payload():
    return {
        "entities": [{"text": "田中さん", "type": "person", "confidence": 0.9}],
        "topics": ["買い物", "週末"],
        "dates": [{"text": "明日", "normalized": None, "role": "mentioned"}],
        "action_items": [
            {
                "text": "牛乳を買う",
                "due_date": None,
                "assignee": None,
                "confidence": 0.8,
            },
        ],
        "event_hints": [{"title": "週末の買い物", "date_text": "明日", "confidence": 0.7}],
        "summary": "買い物について相談している。",
        "confidence": 0.86,
    }


def seed_line_message(
    db_path,
    text="明日、田中さんと買い物に行く。牛乳を買う。",
):
    storage = initialize_database(db_path)
    try:
        return storage.line_messages.insert_message(
            source_item_id=None,
            conversation_id="fixture-room",
            message_id="fixture-message-1",
            sender_id="fixture-speaker",
            sent_at="2026-05-24T10:00:00",
            message_type="text",
            body_text=text,
        )
    finally:
        storage.close()


def seed_note(db_path, title="買い物メモ", body="明日、田中さんと牛乳を買う。"):
    storage = initialize_database(db_path)
    try:
        return storage.notes.insert_note(
            source_item_id=None,
            note_id=f"fixture-note-{title}",
            title=title,
            body_text=body,
        )
    finally:
        storage.close()


def test_text_understanding_annotates_line_message_without_overwriting_original(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    message_id = seed_line_message(db_path)
    client = FakeTextUnderstandingClient(payload=extraction_payload(), model="fake-ja")

    result = annotate_text(db_path, source="line", client=client, model_id="fake-ja")

    storage = initialize_database(db_path)
    try:
        annotations = storage.text_annotations.list()
        row = annotations[0]

        assert result.selected == 1
        assert result.annotated == 1
        assert result.errors == 0
        assert len(client.requests) == 1
        assert client.requests[0].source_type == "line"
        assert storage.line_messages.get(message_id)["body_text"].startswith("明日")
        assert row["source_table"] == "line_messages"
        assert row["source_id"] == message_id
        assert row["summary"] == "買い物について相談している。"
        assert row["confidence"] == 0.86
        assert row["model_id"] == "fake-ja"
        assert json.loads(row["entities_json"])[0]["text"] == "田中さん"
        assert json.loads(row["topics_json"]) == ["買い物", "週末"]
        assert json.loads(row["dates_json"])[0]["text"] == "明日"
        assert json.loads(row["action_items_json"])[0]["text"] == "牛乳を買う"
        assert json.loads(row["event_hints_json"])[0]["title"] == "週末の買い物"
    finally:
        storage.close()


def test_text_understanding_is_resume_safe(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    seed_line_message(db_path)

    first = annotate_text(
        db_path,
        source="line",
        client=FakeTextUnderstandingClient(payload=extraction_payload()),
        model_id="fake-ja",
    )
    second = annotate_text(
        db_path,
        source="line",
        client=FakeTextUnderstandingClient(payload=extraction_payload()),
        model_id="fake-ja",
    )

    storage = initialize_database(db_path)
    try:
        assert first.annotated == 1
        assert second.selected == 0
        assert second.annotated == 0
        assert len(storage.text_annotations.list()) == 1
    finally:
        storage.close()


def test_text_understanding_annotates_notes_with_limit_and_batch_size(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    seed_note(db_path, title="メモ1")
    seed_note(db_path, title="メモ2")

    result = annotate_text(
        db_path,
        source="notes",
        client=FakeTextUnderstandingClient(payload=extraction_payload()),
        model_id="fake-ja",
        limit=1,
        batch_size=1,
    )

    storage = initialize_database(db_path)
    try:
        annotations = storage.text_annotations.list()

        assert result.selected == 1
        assert result.annotated == 1
        assert len(annotations) == 1
        assert annotations[0]["source_table"] == "notes"
    finally:
        storage.close()


def test_text_understanding_rejects_malformed_or_untrusted_json():
    with pytest.raises(TextExtractionError):
        parse_text_understanding_response(TextUnderstandingResponse(json_text="not json"))

    bad_payload = extraction_payload()
    bad_payload["unexpected"] = []
    with pytest.raises(TextExtractionError):
        parse_text_understanding_response(
            TextUnderstandingResponse(json_text=json.dumps(bad_payload, ensure_ascii=False)),
        )

    bad_confidence = extraction_payload()
    bad_confidence["confidence"] = 2.0
    with pytest.raises(TextExtractionError):
        parse_text_understanding_response(
            TextUnderstandingResponse(json_text=json.dumps(bad_confidence, ensure_ascii=False)),
        )


def test_text_understanding_counts_invalid_model_output_as_error(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    seed_line_message(db_path)

    result = annotate_text(
        db_path,
        source="line",
        client=FakeTextUnderstandingClient(json_text="{}"),
        model_id="fake-ja",
    )

    storage = initialize_database(db_path)
    try:
        assert result.selected == 1
        assert result.annotated == 0
        assert result.errors == 1
        assert storage.text_annotations.list() == []
    finally:
        storage.close()


def test_text_understanding_cli_is_privacy_safe(capsys, tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    private_text = "秘密の予定: 明日、田中さんと会う。"
    seed_line_message(db_path, text=private_text)

    exit_code = main(
        [
            "annotate",
            "text",
            "--source",
            "line",
            "--db",
            str(db_path),
            "--client",
            "fake",
            "--limit",
            "1",
        ],
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Text annotation complete" in output
    assert "annotated=1" in output
    assert private_text not in output
    assert "田中さん" not in output
