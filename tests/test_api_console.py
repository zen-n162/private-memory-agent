import json

from private_memory_agent.api.console import (
    ChatConsoleOptions,
    build_system_status,
    run_chat_console_query,
)
from private_memory_agent.api.ui import agent_console_html
from private_memory_agent.retrieval import index_text
from private_memory_agent.storage import initialize_database


def _insert_synthetic_line(db_path, text="研究 synthetic private evidence"):
    storage = initialize_database(db_path)
    try:
        storage.line_messages.insert_message(
            source_item_id=None,
            conversation_id="console-fixture-room",
            message_id="console-line-1",
            sender_id="console-speaker",
            sent_at="2026-05-24T09:00:00",
            message_type="text",
            body_text=text,
        )
    finally:
        storage.close()
    index_text(db_path)


def test_agent_console_html_is_self_contained_and_points_to_chat_api():
    html = agent_console_html()

    assert "/api/chat/query" in html
    assert "/api/system/status" in html
    assert "leader_plan" in html
    assert "show_snippets" in html
    assert "https://" not in html
    assert "cdn.jsdelivr" not in html


def test_chat_console_default_response_hides_answer_and_raw_evidence(
    temp_config_factory,
    tmp_path,
):
    db_path = tmp_path / "console.sqlite3"
    _insert_synthetic_line(db_path, text="研究 raw private console body")

    payload = run_chat_console_query(
        ChatConsoleOptions(
            config_dir=temp_config_factory(),
            db_path=db_path,
            question="研究",
            mode="fake-model",
            sources=("line",),
        ),
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["ok"] is True
    assert payload["answer"]["answer_succeeded"] is True
    assert payload["answer"]["conclusion"] is None
    assert payload["privacy"]["answer_hidden"] is True
    assert payload["privacy"]["snippets_hidden"] is True
    assert payload["evidence"]
    assert payload["trace"]["plan_created"] is True
    assert "raw private console body" not in serialized


def test_chat_console_show_answer_displays_fake_answer_without_snippets(
    temp_config_factory,
    tmp_path,
):
    db_path = tmp_path / "console.sqlite3"
    _insert_synthetic_line(db_path, text="研究 raw evidence remains hidden")

    payload = run_chat_console_query(
        ChatConsoleOptions(
            config_dir=temp_config_factory(),
            db_path=db_path,
            question="研究",
            mode="fake-model",
            sources=("line",),
            show_answer=True,
        ),
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["answer"]["conclusion"] is not None
    assert "Retrieved local evidence is sufficient" in payload["answer"]["conclusion"]
    assert "raw evidence remains hidden" not in serialized
    assert all("snippet" not in item for item in payload["evidence"])
    assert any("show_answer is enabled" in warning for warning in payload["warnings"])


def test_chat_console_show_snippets_is_explicit_and_truncated(
    temp_config_factory,
    tmp_path,
):
    db_path = tmp_path / "console.sqlite3"
    _insert_synthetic_line(db_path, text="研究 " + ("synthetic detail " * 30))

    payload = run_chat_console_query(
        ChatConsoleOptions(
            config_dir=temp_config_factory(),
            db_path=db_path,
            question="研究",
            mode="retrieval-only",
            sources=("line",),
            show_snippets=True,
            snippet_chars=60,
        ),
    )

    snippets = [item.get("snippet", "") for item in payload["evidence"] if item.get("snippet")]
    assert snippets
    assert len(snippets[0]) <= 60
    assert payload["privacy"]["snippets_hidden"] is False
    assert any("show_snippets is enabled" in warning for warning in payload["warnings"])


def test_chat_console_system_status_is_privacy_safe(temp_config_factory, tmp_path):
    db_path = tmp_path / "console.sqlite3"
    _insert_synthetic_line(db_path, text="研究 status private content")

    payload = build_system_status(
        config_dir=temp_config_factory(),
        db_path=db_path,
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["ok"] is True
    assert payload["localhost_only"] is True
    assert payload["db_exists"] is True
    assert payload["counts"]["line_messages_count"] == 1
    assert "status private content" not in serialized
    assert str(tmp_path) not in serialized
