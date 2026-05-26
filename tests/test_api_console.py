import json
import time

from private_memory_agent.api.contract import build_chat_error_payload, ensure_chat_response_contract
from private_memory_agent.api.console import (
    ChatConsoleOptions,
    build_system_status,
    run_chat_console_query,
)
from private_memory_agent.api.runs import ChatRunRecord, ChatRunRegistry
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
    assert "renderCompletedStatus" in html
    assert "renderFailedStatus" in html
    assert "Invalid API response: missing field" in html
    assert "リクエスト形式の問題で実行前に失敗しました。" in html
    assert "Agent trace was not created because execution stopped before agent runtime." in html
    assert "mode=undefined" not in html
    assert "Agent の unknown" not in html
    assert "詳細ログを表示" in html
    assert "未使用のTool/Modelを表示" in html
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
    assert payload["run_id"]
    assert payload["request_id"] == payload["run_id"]
    assert payload["mode"] == "fake-model"
    assert payload["answer_succeeded"] is True
    assert payload["answer_state"] == "visible"
    assert payload["failure_stage"] is None
    assert payload["current_status"]["status"] == "succeeded"
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


def test_chat_error_payload_has_stable_contract_and_privacy_defaults():
    payload = build_chat_error_payload(
        mode="invalid-mode",
        failure_stage="request_validation",
        failure_actor="ChatAPI",
        failed_action="validate_chat_request",
        error_class="InvalidRequest",
        error_message="invalid chat request; check required fields and allowed values",
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["ok"] is False
    assert payload["mode"] == "unknown"
    assert payload["answer_state"] == "not_generated"
    assert payload["answer_succeeded"] is False
    assert payload["failure_stage"] == "request_validation"
    assert payload["failure_actor"] == "ChatAPI"
    assert payload["current_status"]["failure_summary"]["failed_stage"] == "request_validation"
    assert payload["trace_events"] == []
    assert payload["trace_summary"]["runtime_event_count"] == 0
    assert payload["privacy"]["local_only"] is True
    assert payload["privacy"]["snippets_hidden"] is True
    assert payload["privacy"]["raw_model_output_hidden"] is True
    assert "path" not in serialized.lower()


def test_chat_contract_fills_missing_success_metadata():
    payload = ensure_chat_response_contract(
        {
            "ok": True,
            "mode": "retrieval-only",
            "answer": {
                "answer_succeeded": False,
                "answer_state": "not_generated",
                "evidence_references": [],
                "used_sources": [],
            },
            "evidence": [],
            "trace": {"runtime_event_count": 0, "plan": {}},
            "trace_events": [],
            "privacy": {},
            "warnings": [],
        },
        run_id="contract-run",
    )

    assert payload["run_id"] == "contract-run"
    assert payload["mode"] == "retrieval-only"
    assert payload["answer_state"] == "not_generated"
    assert payload["candidate_dates"] == []
    assert payload["current_status"]["status"] == "succeeded"
    assert payload["trace_summary"]["runtime_event_count"] == 0


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
    assert final_status["completion_summary"]["summary_status"] == "done"
    assert final_status["completion_summary"]["answer_succeeded"] is True
    assert final_status["completion_summary"]["evidence_reference_count"] >= 1
    assert final_status["completion_summary"]["major_models_used"]
    assert all(
        "not used" not in item
        for item in final_status["completion_summary"]["major_models_used"]
    )
    assert final_status["current_step"]
    assert events["trace_events"]
    assert result["answer"]["answer_succeeded"] is True
    assert "FakeLeaderModel" in events["model_usage_summary"]
    assert "registry private evidence" not in serialized


def test_chat_run_registry_failed_status_has_safe_failure_summary(tmp_path):
    registry = ChatRunRegistry()
    recorder = AgentTraceRecorder(run_id="failed-run")
    recorder.event(
        actor_type="leader_model",
        actor_name="DeepSeek Leader",
        stage="answer_synthesis",
        action="generate_structured_answer",
        status="failed",
        error_class="ModelRuntimeError",
        safe_error_message="model endpoint request timed out",
    )
    record = ChatRunRecord(
        run_id="failed-run",
        options=ChatConsoleOptions(question="秘密の質問", db_path=tmp_path / "none.sqlite3"),
        recorder=recorder,
        status="failed",
        error_class="ModelRuntimeError",
        safe_error_message="model endpoint request timed out",
    )
    registry._runs["failed-run"] = record

    status = registry.status("failed-run")
    result = registry.result("failed-run")
    serialized = json.dumps({"status": status, "result": result}, ensure_ascii=False)

    assert status["status"] == "failed"
    assert status["mode"] == "retrieval-only"
    assert status["failure_summary"]["failed_actor"] == "DeepSeek Leader"
    assert status["failure_summary"]["error_class"] == "ModelRuntimeError"
    assert "timed out" in status["failure_summary"]["safe_error_message"]
    assert "retrieval-only" in status["failure_summary"]["suggested_next_action"]
    assert result["ok"] is False
    assert result["mode"] == "retrieval-only"
    assert result["answer_state"] == "not_generated"
    assert result["current_status"]["failure_summary"]["failed_actor"] == "DeepSeek Leader"
    assert result["privacy"]["local_only"] is True
    assert "秘密の質問" not in serialized


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
