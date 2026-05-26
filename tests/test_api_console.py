import json
import time
from urllib.error import URLError

from private_memory_agent.api.contract import (
    CHAT_API_RESPONSE_SCHEMA_VERSION,
    CHAT_UI_RESPONSE_SCHEMA_VERSION,
    REQUIRED_CHAT_RESPONSE_KEYS,
    build_chat_error_payload,
    ensure_chat_response_contract,
)
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


class FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = payload
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _leader_models_yaml(model_root):
    return "\n".join(
        [
            f"model_root: {model_root}",
            "leader:",
            "  provider: llama_cpp",
            "  role: leader_reasoning",
            "  model_dir: leader-model",
            "  enabled: true",
            "  endpoint_url: http://127.0.0.1:8111/v1",
            "  served_model_name: served-leader.gguf",
            "  api_format: openai-compatible",
            "  timeout_seconds: 1",
            "  request_timeout_seconds: 77",
            "  retries: 0",
        ],
    )


def _insert_synthetic_line(db_path, text="研究 synthetic private evidence"):
    storage = initialize_database(db_path)
    try:
        message_id = storage.line_messages.insert_message(
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
    return message_id


def _assert_complete_chat_contract(payload, *, mode):
    missing = [key for key in REQUIRED_CHAT_RESPONSE_KEYS if key not in payload]
    assert missing == []
    assert payload["mode"] == mode
    assert "undefined" not in json.dumps(payload, ensure_ascii=False)
    assert payload["privacy"]["local_only"] is True
    assert isinstance(payload["trace_events"], list)
    assert isinstance(payload["current_status"], dict)
    assert isinstance(payload["candidate_dates"], list)
    assert isinstance(payload["evidence"], list)
    if payload["ok"] is True:
        assert payload["failure_stage"] is None
        assert payload["current_status"]["status"] == "succeeded"
        assert payload["current_status"].get("failure_summary") is None
    if payload["answer_succeeded"] is True:
        assert payload["failure_stage"] != "answer_generation"


def test_agent_console_html_is_self_contained_and_points_to_chat_api():
    html = agent_console_html()

    assert "/api/chat/query" in html
    assert "/api/system/status" in html
    assert "leader_plan" in html
    assert "show_snippets" in html
    assert 'name="source" value="photos" checked' in html
    assert 'name="source" value="line" checked' in html
    assert 'name="source" value="notes"> notes' in html
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
    assert "候補日は取得できましたが、DeepSeekによる最終回答生成で失敗しました。" in html
    assert "Status mismatch detected." in html
    assert "recovered_failure_count" in html
    assert "mode=undefined" not in html
    assert "Agent の unknown" not in html
    assert "詳細ログを表示" in html
    assert "未使用のTool/Modelを表示" in html
    assert "/api/chat/query/start" in html
    assert "/api/chat/runs/${runId}/status" in html
    assert "/api/chat/runs/${runId}/result" in html
    assert "fetchFinalResult" in html
    assert "renderFinalResult" in html
    assert "isChatRunNotReady" in html
    assert "rendered_payload_source" in html
    assert "少なくとも1つのsourceを選択してください。" in html
    assert "Run status succeeded but result is not ready." in html
    assert "新しい実行中です。完了後に結果を更新します。" in html
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
    _assert_complete_chat_contract(payload, mode="fake-model")
    assert payload["run_id"]
    assert payload["request_id"] == payload["run_id"]
    assert payload["mode"] == "fake-model"
    assert payload["answer_succeeded"] is True
    assert payload["evidence_builder_succeeded"] is True
    assert payload["answer_synthesis_succeeded"] is True
    assert payload["evidence_count"] > 0
    assert payload["evidence_reference_count"] > 0
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


def test_chat_console_retrieval_only_has_complete_contract(temp_config_factory, tmp_path):
    db_path = tmp_path / "console.sqlite3"
    _insert_synthetic_line(db_path, text="研究 retrieval-only private evidence")

    payload = run_chat_console_query(
        ChatConsoleOptions(
            config_dir=temp_config_factory(),
            db_path=db_path,
            question="研究",
            mode="retrieval-only",
            sources=("line",),
        ),
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    _assert_complete_chat_contract(payload, mode="retrieval-only")
    assert payload["evidence_builder_succeeded"] is True
    assert payload["answer_synthesis_succeeded"] is False
    assert payload["evidence_count"] > 0
    assert payload["answer_succeeded"] is False
    assert payload["answer_state"] == "not_generated"
    assert payload["failure_stage"] is None
    assert payload["trace_events"][0]["actor_name"] == "ChatConsoleRequest"
    assert "retrieval-only private evidence" not in serialized


def test_chat_console_real_model_success_has_complete_contract(
    monkeypatch,
    temp_config_factory,
    tmp_path,
):
    model_root = tmp_path / "models"
    config_dir = temp_config_factory(
        model_root=model_root,
        models_yaml=_leader_models_yaml(model_root),
    )
    db_path = tmp_path / "console.sqlite3"
    message_id = _insert_synthetic_line(db_path, text="研究 real model private evidence")

    def fake_urlopen(request, data=None, *, timeout=None):
        if request.get_method() == "GET":
            return FakeHTTPResponse({"data": [{"id": "served-leader.gguf"}]})
        payload = {
            "conclusion": "synthetic grounded answer",
            "evidence_references": [f"line_messages:{message_id}"],
            "confidence": 0.4,
            "unknowns": ["synthetic uncertainty"],
            "used_sources": ["line"],
        }
        return FakeHTTPResponse(
            {
                "model": "served-leader.gguf",
                "choices": [{"message": {"content": json.dumps(payload)}}],
            },
        )

    monkeypatch.setattr("private_memory_agent.runtime.clients.urlopen", fake_urlopen)

    payload = run_chat_console_query(
        ChatConsoleOptions(
            config_dir=config_dir,
            db_path=db_path,
            question="研究",
            mode="real-model",
            sources=("line",),
            leader_plan=False,
        ),
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    _assert_complete_chat_contract(payload, mode="real-model")
    assert payload["ok"] is True
    assert payload["answer_succeeded"] is True
    assert payload["failure_stage"] is None
    assert payload["current_status"]["status"] == "succeeded"
    assert payload["trace_events"][0]["actor_name"] == "ChatConsoleRequest"
    assert "real model private evidence" not in serialized


def test_chat_console_real_model_runtime_error_has_complete_contract(
    monkeypatch,
    temp_config_factory,
    tmp_path,
):
    model_root = tmp_path / "models"
    config_dir = temp_config_factory(
        model_root=model_root,
        models_yaml=_leader_models_yaml(model_root),
    )
    db_path = tmp_path / "console.sqlite3"
    _insert_synthetic_line(db_path, text="研究 timeout private evidence")

    def fake_urlopen(request, data=None, *, timeout=None):
        if request.get_method() == "GET":
            return FakeHTTPResponse({"data": [{"id": "served-leader.gguf"}]})
        raise TimeoutError

    monkeypatch.setattr("private_memory_agent.runtime.clients.urlopen", fake_urlopen)

    payload = run_chat_console_query(
        ChatConsoleOptions(
            config_dir=config_dir,
            db_path=db_path,
            question="研究",
            mode="real-model",
            sources=("line",),
            leader_plan=False,
        ),
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    _assert_complete_chat_contract(payload, mode="real-model")
    assert payload["ok"] is False
    assert payload["evidence_builder_succeeded"] is True
    assert payload["answer_synthesis_succeeded"] is False
    assert payload["evidence_count"] > 0
    assert payload["evidence_reference_count"] == 0
    assert payload["answer_succeeded"] is False
    assert payload["answer_state"] == "not_generated"
    assert payload["failure_stage"] == "answer_generation"
    assert payload["failure_actor"] == "DeepSeek Leader"
    assert payload["error_class"] == "ModelRuntimeError"
    assert payload["current_status"]["status"] == "failed"
    assert payload["current_status"]["failure_summary"]["failed_stage"] == "answer_generation"
    assert "timeout private evidence" not in serialized


def test_chat_console_real_model_answer_validation_error_has_complete_contract(
    monkeypatch,
    temp_config_factory,
    tmp_path,
):
    model_root = tmp_path / "models"
    config_dir = temp_config_factory(
        model_root=model_root,
        models_yaml=_leader_models_yaml(model_root),
    )
    db_path = tmp_path / "console.sqlite3"
    _insert_synthetic_line(db_path, text="研究 validation private evidence")

    def fake_urlopen(request, data=None, *, timeout=None):
        if request.get_method() == "GET":
            return FakeHTTPResponse({"data": [{"id": "served-leader.gguf"}]})
        return FakeHTTPResponse(
            {
                "model": "served-leader.gguf",
                "choices": [{"message": {"content": "not json"}}],
            },
        )

    monkeypatch.setattr("private_memory_agent.runtime.clients.urlopen", fake_urlopen)

    payload = run_chat_console_query(
        ChatConsoleOptions(
            config_dir=config_dir,
            db_path=db_path,
            question="研究",
            mode="real-model",
            sources=("line",),
            leader_plan=False,
        ),
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    _assert_complete_chat_contract(payload, mode="real-model")
    assert payload["ok"] is False
    assert payload["evidence_builder_succeeded"] is True
    assert payload["answer_synthesis_succeeded"] is False
    assert payload["evidence_count"] > 0
    assert payload["failure_stage"] == "answer_validation"
    assert payload["failure_actor"] == "DeepSeek Leader"
    assert payload["error_class"] == "AnswerValidationError"
    assert payload["current_status"]["failure_summary"]["failed_stage"] == "answer_validation"
    assert "validation private evidence" not in serialized


def test_chat_console_real_model_preflight_failure_has_complete_contract(
    monkeypatch,
    temp_config_factory,
    tmp_path,
):
    model_root = tmp_path / "models"
    config_dir = temp_config_factory(
        model_root=model_root,
        models_yaml=_leader_models_yaml(model_root),
    )
    db_path = tmp_path / "console.sqlite3"
    _insert_synthetic_line(db_path, text="研究 preflight private evidence")

    def fake_urlopen(request, data=None, *, timeout=None):
        raise URLError("not ready")

    monkeypatch.setattr("private_memory_agent.runtime.clients.urlopen", fake_urlopen)

    payload = run_chat_console_query(
        ChatConsoleOptions(
            config_dir=config_dir,
            db_path=db_path,
            question="研究",
            mode="real-model",
            sources=("line",),
            leader_plan=False,
        ),
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    _assert_complete_chat_contract(payload, mode="real-model")
    assert payload["ok"] is False
    assert payload["failure_stage"] == "preflight"
    assert payload["failure_actor"] == "DeepSeek Leader"
    assert payload["error_class"] == "ModelRuntimeError"
    assert "pma models ping leader" in payload["current_status"]["failure_summary"]["suggested_next_action"]
    assert payload["trace_events"][0]["actor_name"] == "ChatConsoleRequest"
    assert "preflight private evidence" not in serialized


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


def test_recovered_leader_failure_with_fallback_is_not_final_failure():
    trace_events = [
        {
            "run_id": "recovered-run",
            "actor_type": "leader_model",
            "actor_name": "DeepSeek Leader",
            "stage": "event_intent_planning",
            "action": "create_event_intent_plan",
            "status": "failed",
            "invocation_type": "live_call",
            "error_class": "ModelRuntimeError",
            "safe_error_message": "event intent planning failed; deterministic fallback will be used",
            "metadata": {},
        },
        {
            "run_id": "recovered-run",
            "actor_type": "tool",
            "actor_name": "DeterministicEventIntentPlanner",
            "stage": "event_intent_planning",
            "action": "create_event_intent_plan",
            "status": "fallback_used",
            "invocation_type": "not_used",
            "metadata": {"fallback_used": True},
        },
        {
            "run_id": "recovered-run",
            "actor_type": "tool",
            "actor_name": "TemporalAnswerSynthesizer",
            "stage": "answer_synthesis",
            "action": "build_temporal_answer",
            "status": "succeeded",
            "metadata": {},
        },
    ]
    payload = ensure_chat_response_contract(
        {
            "ok": True,
            "mode": "real-model",
            "answer": {
                "answer_succeeded": True,
                "answer_state": "visible",
                "conclusion": "synthetic answer",
                "confidence": 0.7,
                "evidence_references": ["media_items:1"],
                "used_sources": ["photos"],
            },
            "evidence": [{"evidence_id": "media_items:1", "source": "photos"}],
            "evidence_display": {"candidate_dates": [{"date": "2025-12-05"}]},
            "trace": {"runtime_event_count": 3, "plan": {}},
            "trace_events": trace_events,
            "failure_stage": "answer_generation",
            "failure_actor": "DeepSeek Leader",
            "error_class": "ModelRuntimeError",
            "error_message": "model endpoint request timed out",
            "current_status": {
                "run_id": "recovered-run",
                "status": "failed",
                "failure_stage": "answer_generation",
                "failure_actor": "DeepSeek Leader",
                "failure_summary": {"summary_status": "failed"},
            },
            "model_usage_summary": {"DeepSeek Leader": {"status": "failed"}},
            "fallback_summary": {
                "fallback_used": True,
                "fallback_count": 1,
                "stages": ["event_intent_planning"],
                "actors": ["DeterministicEventIntentPlanner"],
            },
            "privacy": {},
            "warnings": [],
        },
        run_id="recovered-run",
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["ok"] is True
    assert payload["answer_succeeded"] is True
    assert payload["answer_synthesis_succeeded"] is True
    assert payload["failure_stage"] is None
    assert payload["failure_actor"] is None
    assert payload["error_class"] is None
    assert payload["current_status"]["status"] == "succeeded"
    assert payload["current_status"]["failure_summary"] is None
    assert payload["current_status"]["display_message"] == "回答を生成しました。"
    assert payload["current_status"]["completion_summary"]["candidate_date_count"] == 1
    assert payload["current_status"]["completion_summary"]["evidence_reference_count"] == 1
    assert payload["current_status"]["completion_summary"]["recovered_failure_count"] == 1
    assert payload["recovered_failure_count"] == 1
    assert payload["recovered_failures"][0]["actor"] == "DeepSeek Leader"
    assert payload["recovered_failures"][0]["fallback_actor"] == "DeterministicEventIntentPlanner"
    assert payload["fallback_summary"]["recovered_failure_count"] == 1
    assert payload["model_usage_summary"]["DeepSeek Leader"]["status"] == "partially_failed_recovered"
    assert payload["model_usage_summary"]["DeepSeek Leader"]["recovered"] == 1
    assert "event intent planning に失敗しました" in serialized
    assert "復旧しました" in serialized
    assert "PRIVATE" not in serialized


def test_failed_leader_planning_without_fallback_remains_failed():
    payload = ensure_chat_response_contract(
        {
            "ok": False,
            "mode": "real-model",
            "answer": {
                "answer_succeeded": False,
                "answer_state": "not_generated",
                "evidence_references": [],
                "used_sources": [],
            },
            "evidence": [],
            "trace": {"runtime_event_count": 1, "plan": {}},
            "trace_events": [
                {
                    "run_id": "planning-failed-run",
                    "actor_type": "leader_model",
                    "actor_name": "DeepSeek Leader",
                    "stage": "event_intent_planning",
                    "action": "create_event_intent_plan",
                    "status": "failed",
                    "invocation_type": "live_call",
                    "error_class": "ModelRuntimeError",
                    "safe_error_message": "event intent planning failed",
                    "metadata": {},
                },
            ],
            "privacy": {},
            "warnings": [],
        },
        run_id="planning-failed-run",
    )

    assert payload["ok"] is False
    assert payload["current_status"]["status"] == "failed"
    assert payload["failure_stage"] == "query_understanding"
    assert payload["failure_actor"] == "DeepSeek Leader"
    assert payload["recovered_failure_count"] == 0


def test_unrecovered_answer_generation_failure_remains_final_failure():
    payload = ensure_chat_response_contract(
        {
            "ok": False,
            "mode": "real-model",
            "answer": {
                "answer_succeeded": False,
                "answer_state": "not_generated",
                "evidence_references": [],
                "used_sources": [],
                "error_class": "ModelRuntimeError",
                "error_message": "model endpoint request timed out",
            },
            "evidence": [{"evidence_id": "line_messages:1", "source": "line"}],
            "trace": {"runtime_event_count": 1, "plan": {}},
            "trace_events": [
                {
                    "run_id": "failed-run",
                    "actor_type": "leader_model",
                    "actor_name": "DeepSeek Leader",
                    "stage": "answer_synthesis",
                    "action": "generate_structured_answer",
                    "status": "failed",
                    "invocation_type": "live_call",
                    "error_class": "ModelRuntimeError",
                    "safe_error_message": "model endpoint request timed out",
                    "metadata": {},
                },
            ],
            "privacy": {},
            "warnings": [],
        },
        run_id="failed-run",
    )

    assert payload["ok"] is False
    assert payload["failure_stage"] == "answer_generation"
    assert payload["failure_actor"] == "DeepSeek Leader"
    assert payload["current_status"]["status"] == "failed"
    assert payload["current_status"]["failure_summary"]["failed_stage"] == "answer_generation"
    assert payload["recovered_failure_count"] == 0


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
    assert final_status["result_ready"] is True
    assert final_status["result_available"] is True
    assert final_status["terminal"] is True
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
    assert result["mode"] == "fake-model"
    assert "FakeLeaderModel" in events["model_usage_summary"]
    assert "registry private evidence" not in serialized


def test_chat_run_registry_not_ready_result_is_pending(tmp_path):
    registry = ChatRunRegistry()
    recorder = AgentTraceRecorder(run_id="running-run")
    recorder.event(
        actor_type="tool",
        actor_name="ChatRunRegistry",
        stage="run_queue",
        action="queue_chat_run",
        status="queued",
    )
    record = ChatRunRecord(
        run_id="running-run",
        options=ChatConsoleOptions(question="秘密の質問", db_path=tmp_path / "none.sqlite3"),
        recorder=recorder,
        status="running",
    )
    registry._runs["running-run"] = record

    result = registry.result("running-run")
    serialized = json.dumps(result, ensure_ascii=False)

    assert result["ok"] is False
    assert result["error_class"] == "ChatRunNotReady"
    assert result["status"] == "running"
    assert result["result_ready"] is False
    assert result["current_status"]["status"] == "running"
    assert result["current_status"]["failure_summary"] is None
    assert "秘密の質問" not in serialized


def test_chat_run_registry_succeeded_without_result_reports_finalizing(tmp_path):
    registry = ChatRunRegistry()
    recorder = AgentTraceRecorder(run_id="handoff-run")
    record = ChatRunRecord(
        run_id="handoff-run",
        options=ChatConsoleOptions(question="秘密の質問", db_path=tmp_path / "none.sqlite3"),
        recorder=recorder,
        status="succeeded",
        result_ready=False,
    )
    registry._runs["handoff-run"] = record

    status = registry.status("handoff-run")
    result = registry.result("handoff-run")
    serialized = json.dumps({"status": status, "result": result}, ensure_ascii=False)

    assert status["status"] == "finalizing"
    assert status["result_ready"] is False
    assert status["terminal"] is False
    assert result["error_class"] == "ChatRunResultInvariantError"
    assert result["status"] == "finalizing"
    assert result["result_ready"] is False
    assert "秘密の質問" not in serialized


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

    result = registry.result("failed-run")
    status = registry.status("failed-run")
    serialized = json.dumps({"status": status, "result": result}, ensure_ascii=False)

    assert status["status"] == "failed"
    assert status["result_ready"] is True
    assert status["terminal"] is True
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
    assert payload["app_version"]
    assert payload["api_response_schema_version"] == CHAT_API_RESPONSE_SCHEMA_VERSION
    assert payload["ui_response_schema_version"] == CHAT_UI_RESPONSE_SCHEMA_VERSION
    assert "git_commit" in payload
    assert payload["localhost_only"] is True
    assert payload["db_exists"] is True
    assert payload["counts"]["line_messages_count"] == 1
    assert "status private content" not in serialized
    assert str(tmp_path) not in serialized
