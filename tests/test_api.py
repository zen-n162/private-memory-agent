import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from private_memory_agent.api import create_app
from private_memory_agent.cli import main
from private_memory_agent.storage import initialize_database

requires_working_testclient = pytest.mark.skipif(
    bool(os.environ.get("CODEX_SANDBOX_NETWORK_DISABLED"))
    and not bool(os.environ.get("PMA_RUN_API_TESTCLIENT")),
    reason="FastAPI TestClient hangs inside the Codex network sandbox",
)


@requires_working_testclient
def test_health_endpoint(temp_config_factory, tmp_path):
    app = create_app(
        db_path=tmp_path / "metadata.sqlite3",
        config_dir=temp_config_factory(),
    )
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["localhost_only"] is True


@requires_working_testclient
def test_local_ui_is_served_without_private_data(temp_config_factory, tmp_path):
    app = create_app(
        db_path=tmp_path / "metadata.sqlite3",
        config_dir=temp_config_factory(),
    )
    client = TestClient(app)

    response = client.get("/ui")
    root_response = client.get("/", follow_redirects=False)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert root_response.status_code in {307, 308}
    assert root_response.headers["location"] == "/ui"
    assert "Private Memory Agent Console" in response.text
    assert "Local-only developer console" in response.text
    assert 'value="photos"' in response.text
    assert 'value="line"' in response.text
    assert 'value="notes"' in response.text
    assert "leader_plan" in response.text
    assert "show_snippets" in response.text
    assert 'id="show-answer" type="checkbox" checked' in response.text
    assert 'fetch("/api/chat/query"' in response.text
    assert 'fetch("/api/system/status"' in response.text
    assert "秘密" not in response.text


@requires_working_testclient
def test_chat_query_endpoint_returns_evidence_console_payload(temp_config_factory, tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        storage.line_messages.insert_message(
            source_item_id=None,
            conversation_id="fixture-room",
            message_id="line-chat-api-1",
            sender_id="fixture-speaker",
            sent_at="2026-05-24T09:00:00",
            message_type="text",
            body_text="研究 chat private body",
        )
    finally:
        storage.close()
    from private_memory_agent.retrieval import index_text

    index_text(db_path)
    app = create_app(db_path=db_path, config_dir=temp_config_factory())
    client = TestClient(app)

    response = client.post(
        "/api/chat/query",
        json={
            "question": "研究",
            "mode": "fake-model",
            "sources": ["line"],
            "show_answer": False,
        },
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["answer"]["answer_succeeded"] is True
    assert payload["answer"]["conclusion"] is None
    assert payload["evidence"]
    assert payload["trace"]["plan_created"] is True
    assert payload["privacy"]["snippets_hidden"] is True
    assert "chat private body" not in response.text


@requires_working_testclient
def test_chat_query_endpoint_defaults_to_visible_answer(temp_config_factory, tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        storage.line_messages.insert_message(
            source_item_id=None,
            conversation_id="fixture-room",
            message_id="line-chat-api-default-answer",
            sender_id="fixture-speaker",
            sent_at="2026-05-24T09:00:00",
            message_type="text",
            body_text="研究 default visible answer private body",
        )
    finally:
        storage.close()
    from private_memory_agent.retrieval import index_text

    index_text(db_path)
    app = create_app(db_path=db_path, config_dir=temp_config_factory())
    client = TestClient(app)

    response = client.post(
        "/api/chat/query",
        json={"question": "研究", "mode": "fake-model", "sources": ["line"]},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["answer"]["answer_succeeded"] is True
    assert payload["answer"]["answer_hidden"] is False
    assert payload["answer"]["conclusion"] is not None
    assert payload["privacy"]["snippets_hidden"] is True
    assert "default visible answer private body" not in response.text


@requires_working_testclient
def test_chat_query_invalid_mode_returns_structured_validation_error(
    temp_config_factory,
    tmp_path,
):
    app = create_app(db_path=tmp_path / "metadata.sqlite3", config_dir=temp_config_factory())
    client = TestClient(app)

    response = client.post(
        "/api/chat/query",
        json={"question": "研究", "mode": "unsupported-mode"},
    )

    payload = response.json()
    assert response.status_code == 400
    assert payload["ok"] is False
    assert payload["mode"] == "unknown"
    assert payload["failure_stage"] == "request_validation"
    assert payload["error_class"] == "InvalidRequest"
    assert payload["answer_state"] == "not_generated"
    assert payload["trace_events"] == []
    assert payload["privacy"]["local_only"] is True


@requires_working_testclient
def test_chat_query_missing_question_returns_structured_validation_error(
    temp_config_factory,
    tmp_path,
):
    app = create_app(db_path=tmp_path / "metadata.sqlite3", config_dir=temp_config_factory())
    client = TestClient(app)

    response = client.post("/api/chat/query/start", json={"mode": "fake-model"})

    payload = response.json()
    assert response.status_code == 400
    assert payload["ok"] is False
    assert payload["failure_stage"] == "request_validation"
    assert payload["current_status"]["failure_summary"]["failed_stage"] == "request_validation"
    assert payload["privacy"]["raw_model_output_hidden"] is True


@requires_working_testclient
def test_system_status_endpoint_returns_count_only(temp_config_factory, tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    initialize_database(db_path).close()
    app = create_app(db_path=db_path, config_dir=temp_config_factory())
    client = TestClient(app)

    response = client.get("/api/system/status")

    payload = response.json()
    assert response.status_code == 200
    assert payload["localhost_only"] is True
    assert payload["db_exists"] is True
    assert str(db_path) not in response.text


@requires_working_testclient
def test_query_endpoint_uses_fake_client_and_redacts(temp_config_factory, tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        storage.line_messages.insert_message(
            source_item_id=None,
            conversation_id="fixture-room",
            message_id="line-api-1",
            sender_id="fixture-speaker",
            sent_at="2026-05-24T09:00:00",
            message_type="text",
            body_text="秘密のローカル検索について話した。",
        )
    finally:
        storage.close()
    app = create_app(db_path=db_path, config_dir=temp_config_factory())
    client = TestClient(app)

    response = client.post(
        "/api/query",
        json={"question": "ローカル検索", "client": "fake", "limit": 2},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["redacted"] is True
    assert payload["question"] == "[redacted]"
    assert payload["answer"]["conclusion"] == "[redacted]"
    assert payload["answer"]["evidence_references"] == ["line_messages:1"]
    assert "秘密" not in response.text


@requires_working_testclient
def test_query_endpoint_applies_source_filters(temp_config_factory, tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        storage.line_messages.insert_message(
            source_item_id=None,
            conversation_id="fixture-room",
            message_id="line-api-filter",
            sender_id="fixture-speaker",
            sent_at="2026-05-24T09:00:00",
            message_type="text",
            body_text="sharedterm line private body",
        )
        storage.notes.insert_note(
            source_item_id=None,
            note_id="note-api-filter",
            title="sharedterm note private title",
            body_text="sharedterm note private body",
            created_at_source="2026-05-24T10:00:00",
        )
    finally:
        storage.close()
    app = create_app(db_path=db_path, config_dir=temp_config_factory())
    client = TestClient(app)

    response = client.post(
        "/api/query",
        json={
            "question": "sharedterm",
            "client": "fake",
            "sources": ["notes"],
            "limit": 5,
        },
    )

    payload = response.json()
    assert response.status_code == 200
    assert [item["source_kind"] for item in payload["evidence"]] == ["notes"]
    assert payload["answer"]["evidence_references"] == ["notes:1"]
    assert "private body" not in response.text
    assert "private title" not in response.text


@requires_working_testclient
def test_query_endpoint_returns_insufficient_evidence_without_private_text(
    temp_config_factory,
    tmp_path,
):
    app = create_app(
        db_path=tmp_path / "metadata.sqlite3",
        config_dir=temp_config_factory(),
    )
    client = TestClient(app)

    response = client.post("/api/query", json={"question": "該当なし", "client": "fake"})

    payload = response.json()
    assert response.status_code == 200
    assert payload["answer"]["confidence"] == 0.0
    assert "Insufficient local evidence" in payload["answer"]["conclusion"]
    assert payload["question"] == "[redacted]"


@requires_working_testclient
def test_ingest_endpoints_return_count_only(temp_config_factory, tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    line_path = tmp_path / "line.txt"
    line_path.write_text(
        "トーク履歴: テスト\n2026/05/24(日)\n09:00\tテスト話者\tこんにちは\n",
        encoding="utf-8",
    )
    note_path = tmp_path / "note.md"
    note_path.write_text("# 秘密メモ\n本文", encoding="utf-8")
    app = create_app(db_path=db_path, config_dir=temp_config_factory())
    client = TestClient(app)

    line_response = client.post(
        "/api/ingest/line",
        json={"path": str(line_path), "dry_run": False},
    )
    note_response = client.post(
        "/api/ingest/notes",
        json={"path": str(note_path), "dry_run": False},
    )

    assert line_response.status_code == 200
    assert note_response.status_code == 200
    assert line_response.json()["messages_imported"] >= 1
    assert line_response.json()["messages_imported"] == line_response.json()["messages_parsed"]
    assert note_response.json()["notes_imported"] == 1
    assert "こんにちは" not in line_response.text
    assert "秘密メモ" not in note_response.text
    assert str(line_path) not in line_response.text
    assert str(note_path) not in note_response.text


@requires_working_testclient
def test_photo_ingest_endpoint_uses_synthetic_fixture(temp_config_factory, tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    fixture_dir = Path(__file__).parent / "fixtures"
    app = create_app(db_path=db_path, config_dir=temp_config_factory())
    client = TestClient(app)

    response = client.post(
        "/api/ingest/photos",
        json={"path": str(fixture_dir), "dry_run": True},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["scanned"] == 1
    assert payload["imported"] == 1
    assert "tiny.png" not in response.text


@requires_working_testclient
def test_events_and_entities_endpoints_redact_private_fields(temp_config_factory, tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    storage = initialize_database(db_path)
    try:
        storage.events.insert_event(
            event_type="tentative",
            title="秘密イベント",
            started_at="2026-05-24T09:00:00+09:00",
            ended_at="2026-05-24T10:00:00+09:00",
            confidence=0.5,
            metadata_json=json.dumps(
                {
                    "status": "tentative",
                    "participants": ["秘密人物"],
                    "places": ["秘密場所"],
                    "topics": ["秘密話題"],
                    "evidence_ids": ["notes:1"],
                    "identity_assertions": False,
                },
                ensure_ascii=False,
            ),
        )
        storage.entities.insert_entity(
            entity_type="person",
            canonical_name="秘密人物",
            display_name="秘密人物",
            metadata_json=json.dumps(
                {
                    "aliases": ["秘密人物"],
                    "alias_norms": ["秘密人物"],
                    "user_confirmed": False,
                    "identity_status": "candidate",
                },
                ensure_ascii=False,
            ),
        )
    finally:
        storage.close()
    app = create_app(db_path=db_path, config_dir=temp_config_factory())
    client = TestClient(app)

    events_response = client.get("/api/events")
    entities_response = client.get("/api/entities")

    assert events_response.status_code == 200
    assert entities_response.status_code == 200
    assert events_response.json()["events"][0]["title"] == "[redacted]"
    assert entities_response.json()["entities"][0]["display_name"] == "[redacted]"
    assert "秘密" not in events_response.text
    assert "秘密" not in entities_response.text


def test_api_serve_rejects_non_loopback_host(capsys):
    exit_code = main(["api", "serve", "--host", "0.0.0.0"])

    assert exit_code == 2
    assert "loopback" in capsys.readouterr().out
