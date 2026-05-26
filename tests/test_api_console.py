import json
import time

from private_memory_agent.api.console import (
    ChatConsoleOptions,
    build_system_status,
    run_chat_console_query,
)
from private_memory_agent.api.runs import ChatRunRegistry
from private_memory_agent.api.schemas import ChatQueryRequest
from private_memory_agent.api.ui import agent_console_html
from private_memory_agent.retrieval import index_text
from private_memory_agent.storage import initialize_database
from private_memory_agent.tracing import AgentTraceRecorder, build_current_status, summarize_model_usage


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
    assert 'id="show-answer" type="checkbox" checked' in html
    assert 'id="show-snippets" type="checkbox"> show_snippets' in html
    assert 'id="show-photo-thumbnails" type="checkbox" checked' in html
    assert 'id="show-full-text" type="checkbox"> show_full_text' in html
    assert 'id="candidate-dates-panel"' in html
    assert "thumbnail-grid" in html
    assert "source-tabs" in html
    assert "tab-button" in html
    assert "Read more" in html
    assert "Show fewer thumbnails" in html
    assert "overflow-wrap: anywhere" in html
    assert "Temporal Diagnostics" in html
    assert "Agent Runtime Trace" in html
    assert "Runtime Timeline" in html
    assert "Model / Tool Summary" in html
    assert 'id="current-status-bar"' in html
    assert "/api/chat/query/start" in html
    assert "/api/chat/runs/${runId}/status" in html
    assert "groupTraceEvents" in html
    assert "status-badge" in html
    assert "parsed_date_range_start" in html
    assert "Answer was generated but hidden because Show answer is off." in html
    assert "Conclusion (unknown / insufficient evidence)" in html
    assert "https://" not in html
    assert "cdn.jsdelivr" not in html


def test_chat_query_schema_defaults_to_show_answer_for_ui():
    request = ChatQueryRequest(question="研究")

    assert request.show_answer is True
    assert request.show_snippets is False
    assert request.show_photo_thumbnails is True
    assert request.show_full_text is False
    assert request.show_raw_model_output is False


def test_chat_console_default_response_shows_answer_but_not_raw_evidence(
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
    assert payload["answer"]["conclusion"] is not None
    assert payload["answer"]["answer_hidden"] is False
    assert payload["answer"]["answer_state"] == "visible"
    assert payload["privacy"]["answer_hidden"] is False
    assert payload["privacy"]["snippets_hidden"] is True
    assert payload["privacy"]["photo_thumbnails_hidden"] is False
    assert payload["evidence_display"]["privacy"]["snippets_hidden"] is True
    assert payload["evidence"]
    assert payload["trace"]["plan_created"] is True
    assert payload["trace_events"]
    assert payload["trace"]["runtime_event_count"] == len(payload["trace_events"])
    assert "FakeLeaderModel" in payload["model_usage_summary"]
    assert "RetrievalService" in payload["tool_usage_summary"]
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
    assert payload["evidence_display"]["groups"]["line"][0]["snippet_hidden"] is True
    assert any("show_answer is enabled" in warning for warning in payload["warnings"])


def test_chat_console_explicit_hidden_answer_has_clear_state(
    temp_config_factory,
    tmp_path,
):
    db_path = tmp_path / "console.sqlite3"
    _insert_synthetic_line(db_path, text="研究 raw hidden evidence")

    payload = run_chat_console_query(
        ChatConsoleOptions(
            config_dir=temp_config_factory(),
            db_path=db_path,
            question="研究",
            mode="fake-model",
            sources=("line",),
            show_answer=False,
        ),
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["answer"]["answer_succeeded"] is True
    assert payload["answer"]["conclusion"] is None
    assert payload["answer"]["answer_hidden"] is True
    assert payload["answer"]["answer_state"] == "hidden"
    assert payload["privacy"]["answer_hidden"] is True
    assert "Retrieved local evidence is sufficient" not in serialized
    assert "raw hidden evidence" not in serialized


def test_chat_console_unknown_answer_is_not_marked_hidden(temp_config_factory, tmp_path):
    db_path = tmp_path / "console.sqlite3"
    initialize_database(db_path).close()

    payload = run_chat_console_query(
        ChatConsoleOptions(
            config_dir=temp_config_factory(),
            db_path=db_path,
            question="no matching evidence",
            mode="fake-model",
            show_answer=True,
        ),
    )

    assert payload["answer"]["answer_hidden"] is False
    if payload["answer"]["answer_succeeded"]:
        assert payload["answer"]["answer_state"] == "unknown"
        assert payload["answer"]["conclusion"] is not None


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
    assert payload["evidence_display"]["groups"]["line"][0]["snippet"]
    assert any("show_snippets is enabled" in warning for warning in payload["warnings"])


def test_chat_console_trace_records_semantic_reranker_and_answer_generation(
    temp_config_factory,
    tmp_path,
):
    db_path = tmp_path / "console.sqlite3"
    _insert_synthetic_line(db_path, text="研究 trace private evidence")

    payload = run_chat_console_query(
        ChatConsoleOptions(
            config_dir=temp_config_factory(),
            db_path=db_path,
            question="研究",
            mode="fake-model",
            sources=("line",),
            semantic=True,
            semantic_model="hash",
            reranker="fake",
        ),
    )
    serialized = json.dumps(payload, ensure_ascii=False)
    stages = {event["stage"] for event in payload["trace_events"]}
    actor_names = {event["actor_name"] for event in payload["trace_events"]}

    assert "semantic_retrieval" in stages
    assert "reranking" in stages
    assert "answer_synthesis" in stages
    assert "FakeLeaderModel" in actor_names
    assert payload["model_usage_summary"]["FakeLeaderModel"]["status"] == "used"
    assert "trace private evidence" not in serialized


def test_trace_recorder_failed_stage_uses_safe_error_message():
    recorder = AgentTraceRecorder(run_id="test-run")
    step_id = recorder.start(
        actor_type="leader_model",
        actor_name="DeepSeek Leader",
        stage="answer_synthesis",
        action="generate_structured_answer",
        invocation_type="live_call",
        safe_input_summary="private prompt hidden",
    )
    recorder.finish(
        step_id,
        status="failed",
        error_class="ModelRuntimeError",
        safe_error_message="model endpoint request timed out",
    )
    events = recorder.to_list()

    assert events[0]["status"] == "failed"
    assert events[0]["safe_error_message"] == "model endpoint request timed out"
    assert "private prompt hidden" in events[0]["safe_input_summary"]
    assert summarize_model_usage(events)["DeepSeek Leader"]["failed"] == 1


def test_current_status_from_trace_is_privacy_safe():
    recorder = AgentTraceRecorder(run_id="status-run")
    step_id = recorder.start(
        actor_type="leader_model",
        actor_name="DeepSeek Leader",
        stage="retrieval_planning",
        action="create_retrieval_plan",
        invocation_type="live_call",
        safe_input_summary="raw question hidden",
    )
    status = build_current_status(
        run_id="status-run",
        status="running",
        events=recorder.to_list(),
        elapsed_ms=1250,
    )

    assert status["status"] == "running"
    assert status["current_step"]["actor_name"] == "DeepSeek Leader"
    assert "検索計画" in status["current_step"]["display_message"]
    assert status["elapsed_ms"] == 1250
    assert "PRIVATE_SECRET" not in json.dumps(status, ensure_ascii=False)
    recorder.finish(step_id)


def test_chat_run_registry_returns_status_events_and_result(
    temp_config_factory,
    tmp_path,
):
    db_path = tmp_path / "console.sqlite3"
    _insert_synthetic_line(db_path, text="研究 registry private evidence")
    registry = ChatRunRegistry()

    started = registry.start(
        ChatConsoleOptions(
            config_dir=temp_config_factory(),
            db_path=db_path,
            question="研究",
            mode="fake-model",
            sources=("line",),
        ),
    )
    run_id = started["run_id"]
    final_status = started
    for _ in range(100):
        final_status = registry.status(run_id)
        if final_status["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.02)
    events = registry.events(run_id)
    result = registry.result(run_id)
    serialized = json.dumps({"status": final_status, "events": events, "result": result}, ensure_ascii=False)

    assert final_status["status"] == "succeeded"
    assert final_status["current_step"]
    assert events["trace_events"]
    assert result["answer"]["answer_succeeded"] is True
    assert "FakeLeaderModel" in events["model_usage_summary"]
    assert "registry private evidence" not in serialized


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
