"""Privacy-safe runtime trace events for local agent execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from time import perf_counter
from typing import Any
from uuid import uuid4

TRACE_PRIVACY_LEVEL = "safe_metadata_only"

RUN_STATUSES = {"idle", "queued", "running", "succeeded", "failed"}

ACTION_DISPLAY_MESSAGES = {
    "queue_chat_run": "実行をキューに入れています...",
    "receive_local_query": "ローカル質問を受け取りました。",
    "preflight_event_intent_planner": "DeepSeek Leader の接続を確認しています...",
    "parse_temporal_expression": "日付範囲を解析しています...",
    "create_event_intent_plan": "DeepSeek Leader がイベント意図と検索方針を作成しています...",
    "use_supplied_event_intent_plan": "既存のイベント意図計画を使用しています...",
    "load_local_config": "ローカル設定を読み込んでいます...",
    "create_retrieval_plan": "DeepSeek Leader が検索計画を作成しています...",
    "fallback_query_path": "決定的 fallback の検索経路に切り替えています...",
    "detect_temporal_event_query": "temporal event query か確認しています...",
    "count_photos_in_date_range": "対象期間の写真 coverage を数えています...",
    "search_photos_by_date_range": "PhotoDateSearchTool で対象期間の写真を検索しています...",
    "use_cached_photo_annotations": "Qwen3-VL の既存 annotation を確認しています...",
    "search_same_range_text_support": "LINE とノートの同期間サポートを検索しています...",
    "check_cached_text_annotations": "Qwen3 Swallow の text annotation 状態を確認しています...",
    "semantic_search": "semantic retrieval の候補を確認しています...",
    "rerank_candidates": "reranker で候補順序を確認しています...",
    "judge_candidate_evidence": "evidence judge が候補の有用性を評価しています...",
    "separate_used_candidate_rejected_evidence": "使用 evidence と弱い候補を分離しています...",
    "event_intent_repair": "retrieval repair が必要か確認しています...",
    "build_temporal_answer": "候補日から temporal answer を組み立てています...",
    "generate_structured_answer": "DeepSeek Leader が構造化回答を生成しています...",
    "validate_answer_payload": "回答 schema と evidence reference を検証しています...",
    "verify_safe_console_payload": "PrivacyGuard が出力を安全化しています...",
    "assemble_console_payload": "UI 表示用の結果を組み立てています...",
}

NEXT_STEP_HINTS = {
    "query_received": "次に query type と検索経路を判定します。",
    "event_intent_planning": "次に source-specific retrieval を実行します。",
    "photo_date_search": "次に cached annotation と日別候補を確認します。",
    "line_notes_temporal_support": "次に candidate date の根拠を評価します。",
    "semantic_retrieval": "次に候補を merge/rerank します。",
    "reranking": "次に evidence judge または回答生成へ進みます。",
    "answer_synthesis": "次に回答検証と privacy filter を行います。",
    "answer_validation": "次に UI 用 payload を作成します。",
    "privacy_filtering": "まもなく結果を表示します。",
}


@dataclass
class AgentTraceEvent:
    """One privacy-safe agent/tool/model execution event."""

    run_id: str
    step_id: str
    parent_step_id: str | None
    timestamp: str
    actor_type: str
    actor_name: str
    stage: str
    action: str
    status: str
    model_id: str | None = None
    provider: str | None = None
    safe_input_summary: str | None = None
    safe_output_summary: str | None = None
    reasoning_summary: str | None = None
    decision_summary: str | None = None
    error_class: str | None = None
    safe_error_message: str | None = None
    duration_ms: int | None = None
    token_input_count: int | None = None
    token_output_count: int | None = None
    privacy_level: str = TRACE_PRIVACY_LEVEL
    invocation_type: str | None = None
    artifact_type: str | None = None
    artifact_model_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "step_id": self.step_id,
            "parent_step_id": self.parent_step_id,
            "timestamp": self.timestamp,
            "actor_type": self.actor_type,
            "actor_name": self.actor_name,
            "model_id": self.model_id,
            "provider": self.provider,
            "stage": self.stage,
            "action": self.action,
            "status": self.status,
            "safe_input_summary": self.safe_input_summary,
            "safe_output_summary": self.safe_output_summary,
            "reasoning_summary": self.reasoning_summary,
            "decision_summary": self.decision_summary,
            "error_class": self.error_class,
            "safe_error_message": self.safe_error_message,
            "duration_ms": self.duration_ms,
            "token_input_count": self.token_input_count,
            "token_output_count": self.token_output_count,
            "privacy_level": self.privacy_level,
            "invocation_type": self.invocation_type,
            "artifact_type": self.artifact_type,
            "artifact_model_id": self.artifact_model_id,
            "metadata": _safe_metadata(self.metadata),
        }


class AgentTraceRecorder:
    """In-memory trace recorder for one local request."""

    def __init__(self, *, run_id: str | None = None) -> None:
        self.run_id = run_id or str(uuid4())
        self._events: list[AgentTraceEvent] = []
        self._starts: dict[str, float] = {}
        self._counter = 0
        self._lock = RLock()

    def start(
        self,
        *,
        actor_type: str,
        actor_name: str,
        stage: str,
        action: str,
        parent_step_id: str | None = None,
        model_id: str | None = None,
        provider: str | None = None,
        safe_input_summary: str | None = None,
        reasoning_summary: str | None = None,
        decision_summary: str | None = None,
        invocation_type: str | None = None,
        artifact_type: str | None = None,
        artifact_model_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        with self._lock:
            step_id = self._next_step_id()
            self._starts[step_id] = perf_counter()
            self._events.append(
                AgentTraceEvent(
                    run_id=self.run_id,
                    step_id=step_id,
                    parent_step_id=parent_step_id,
                    timestamp=_timestamp(),
                    actor_type=actor_type,
                    actor_name=actor_name,
                    model_id=model_id,
                    provider=provider,
                    stage=stage,
                    action=action,
                    status="running",
                    safe_input_summary=safe_input_summary,
                    reasoning_summary=reasoning_summary,
                    decision_summary=decision_summary,
                    invocation_type=invocation_type,
                    artifact_type=artifact_type,
                    artifact_model_id=artifact_model_id,
                    metadata=_safe_metadata(metadata or {}),
                ),
            )
            return step_id

    def finish(
        self,
        step_id: str,
        *,
        status: str = "succeeded",
        safe_output_summary: str | None = None,
        reasoning_summary: str | None = None,
        decision_summary: str | None = None,
        error_class: str | None = None,
        safe_error_message: str | None = None,
        token_input_count: int | None = None,
        token_output_count: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            event = self._find(step_id)
            if event is None:
                return
            started = self._starts.pop(step_id, None)
            if started is not None:
                event.duration_ms = max(0, int((perf_counter() - started) * 1000))
            event.status = status
            if safe_output_summary is not None:
                event.safe_output_summary = safe_output_summary
            if reasoning_summary is not None:
                event.reasoning_summary = reasoning_summary
            if decision_summary is not None:
                event.decision_summary = decision_summary
            if error_class is not None:
                event.error_class = error_class
            if safe_error_message is not None:
                event.safe_error_message = safe_error_message
            if token_input_count is not None:
                event.token_input_count = token_input_count
            if token_output_count is not None:
                event.token_output_count = token_output_count
            if metadata:
                event.metadata.update(_safe_metadata(metadata))

    def event(
        self,
        *,
        actor_type: str,
        actor_name: str,
        stage: str,
        action: str,
        status: str = "succeeded",
        parent_step_id: str | None = None,
        model_id: str | None = None,
        provider: str | None = None,
        safe_input_summary: str | None = None,
        safe_output_summary: str | None = None,
        reasoning_summary: str | None = None,
        decision_summary: str | None = None,
        error_class: str | None = None,
        safe_error_message: str | None = None,
        duration_ms: int | None = None,
        invocation_type: str | None = None,
        artifact_type: str | None = None,
        artifact_model_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        with self._lock:
            step_id = self._next_step_id()
            self._events.append(
                AgentTraceEvent(
                    run_id=self.run_id,
                    step_id=step_id,
                    parent_step_id=parent_step_id,
                    timestamp=_timestamp(),
                    actor_type=actor_type,
                    actor_name=actor_name,
                    model_id=model_id,
                    provider=provider,
                    stage=stage,
                    action=action,
                    status=status,
                    safe_input_summary=safe_input_summary,
                    safe_output_summary=safe_output_summary,
                    reasoning_summary=reasoning_summary,
                    decision_summary=decision_summary,
                    error_class=error_class,
                    safe_error_message=safe_error_message,
                    duration_ms=duration_ms,
                    invocation_type=invocation_type,
                    artifact_type=artifact_type,
                    artifact_model_id=artifact_model_id,
                    metadata=_safe_metadata(metadata or {}),
                ),
            )
            return step_id

    def to_list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [event.to_dict() for event in self._events]

    @property
    def events(self) -> tuple[AgentTraceEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def _next_step_id(self) -> str:
        self._counter += 1
        return f"step_{self._counter:03d}"

    def _find(self, step_id: str) -> AgentTraceEvent | None:
        for event in reversed(self._events):
            if event.step_id == step_id:
                return event
        return None


def summarize_model_usage(events: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> dict[str, Any]:
    """Summarize model events without private payloads."""

    summary: dict[str, dict[str, Any]] = {}
    recovered_by_actor: dict[str, int] = {}
    for item in summarize_recovered_failures(events)["recovered_failures"]:
        actor = str(item.get("actor") or "")
        if actor:
            recovered_by_actor[actor] = recovered_by_actor.get(actor, 0) + 1
    for event in events:
        actor_type = str(event.get("actor_type") or "")
        if actor_type not in {"leader_model", "specialist_model", "embedding_model", "reranker"}:
            continue
        name = str(event.get("actor_name") or event.get("model_id") or "unknown_model")
        item = summary.setdefault(
            name,
            {
                "status": "not_used",
                "live_calls": 0,
                "fake_calls": 0,
                "cached_artifacts": 0,
                "not_used": 0,
                "failed": 0,
                "recovered": 0,
                "model_ids": [],
                "artifact_types": [],
                "stages": [],
            },
        )
        invocation = event.get("invocation_type")
        if invocation == "live_call" and event.get("status") in {"succeeded", "failed", "running"}:
            item["live_calls"] += 1
        elif invocation == "fake_call":
            item["fake_calls"] += 1
        elif invocation == "cached_artifact":
            item["cached_artifacts"] += 1
        elif invocation == "not_used":
            item["not_used"] += 1
        if event.get("status") == "failed":
            item["failed"] += 1
            item["recovered"] = recovered_by_actor.get(name, 0)
        _append_unique(item["model_ids"], event.get("model_id") or event.get("artifact_model_id"))
        _append_unique(item["artifact_types"], event.get("artifact_type"))
        _append_unique(item["stages"], event.get("stage"))
        item["status"] = _summary_status(item)
    return summary


def summarize_tool_usage(events: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> dict[str, Any]:
    """Summarize non-model tool/retriever/validator events."""

    summary: dict[str, dict[str, Any]] = {}
    for event in events:
        actor_type = str(event.get("actor_type") or "")
        if actor_type not in {"tool", "retriever", "privacy_guard", "validator"}:
            continue
        name = str(event.get("actor_name") or "unknown_tool")
        item = summary.setdefault(
            name,
            {"succeeded": 0, "failed": 0, "skipped": 0, "fallback_used": 0, "stages": []},
        )
        status = str(event.get("status") or "succeeded")
        if status in item:
            item[status] += 1
        _append_unique(item["stages"], event.get("stage"))
    return summary


def summarize_fallbacks(events: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> dict[str, Any]:
    """Return a compact fallback summary."""

    fallback_events = [
        event
        for event in events
        if event.get("status") == "fallback_used"
        or bool((event.get("metadata") or {}).get("fallback_used"))
    ]
    return {
        "fallback_used": bool(fallback_events),
        "fallback_count": len(fallback_events),
        "stages": [str(event.get("stage")) for event in fallback_events],
        "actors": [str(event.get("actor_name")) for event in fallback_events],
        "recovered_failure_count": summarize_recovered_failures(events)["recovered_failure_count"],
    }


def summarize_recovered_failures(
    events: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    """Return failed trace events that were followed by an explicit fallback.

    This deliberately treats recovered failures as trace metadata only. Final
    run success/failure is decided by the top-level answer/result contract.
    """

    event_list = list(events)
    recovered: list[dict[str, Any]] = []
    for index, event in enumerate(event_list):
        if event.get("status") != "failed":
            continue
        fallback = _next_fallback_event(event_list, start=index + 1)
        if fallback is None:
            continue
        recovered.append(
            {
                "actor": str(event.get("actor_name") or "unknown"),
                "stage": str(event.get("stage") or "unknown"),
                "action": str(event.get("action") or "unknown"),
                "error_class": event.get("error_class"),
                "fallback_actor": str(fallback.get("actor_name") or "unknown"),
                "fallback_stage": str(fallback.get("stage") or "unknown"),
                "fallback_action": str(fallback.get("action") or "unknown"),
                "recovered": True,
            },
        )
    return {
        "recovered_failure_count": len(recovered),
        "recovered_failures": recovered,
    }


def build_current_status(
    *,
    run_id: str,
    status: str,
    events: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    elapsed_ms: int = 0,
    warnings: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Build a compact current-status payload from trace events."""

    safe_status = status if status in RUN_STATUSES else "running"
    event_list = list(events)
    running = [event for event in event_list if event.get("status") == "running"]
    failed = [event for event in event_list if event.get("status") == "failed"]
    current = (
        running[-1]
        if running
        else (
            failed[-1]
            if safe_status == "failed" and failed
            else (event_list[-1] if event_list else None)
        )
    )
    if failed and safe_status not in {"failed", "succeeded"}:
        safe_status = "failed"
    completed = [
        event
        for event in event_list
        if event.get("status") in {"succeeded", "failed", "skipped", "fallback_used"}
    ]
    current_step = (
        _current_step_payload(current, index=event_list.index(current) + 1, total=len(event_list))
        if current is not None
        else None
    )
    recent_steps = [
        _recent_step_payload(event, index=event_list.index(event) + 1)
        for event in completed[-3:]
    ]
    next_step_hint = _next_step_hint(current, safe_status=safe_status)
    recovered_summary = summarize_recovered_failures(event_list)
    return {
        "run_id": run_id,
        "status": safe_status,
        "current_step": current_step,
        "recent_steps": recent_steps,
        "next_step_hint": next_step_hint,
        "elapsed_ms": max(0, int(elapsed_ms)),
        "warnings": list(warnings),
        "model_usage_summary": summarize_model_usage(event_list),
        "tool_usage_summary": summarize_tool_usage(event_list),
        "fallback_summary": summarize_fallbacks(event_list),
        "recovered_failure_count": recovered_summary["recovered_failure_count"],
        "recovered_failures": recovered_summary["recovered_failures"],
    }


def trace_display_message(event: dict[str, Any] | None) -> str:
    """Return a privacy-safe Japanese display message for a trace event."""

    if not event:
        return "待機中です。"
    action = str(event.get("action") or "")
    status = str(event.get("status") or "")
    actor = str(event.get("actor_name") or "Agent")
    if status == "failed":
        message = str(event.get("safe_error_message") or "安全なエラー情報を確認してください。")
        return f"{actor} の処理で失敗しました。{message}"
    if status == "skipped":
        return f"{actor} はこの実行では使用されませんでした。"
    if status == "fallback_used":
        base = ACTION_DISPLAY_MESSAGES.get(action, f"{actor} が fallback を使用しました。")
        return f"{base} fallback を使用しています。"
    return ACTION_DISPLAY_MESSAGES.get(action, f"{actor} が {action or '処理'} を実行しています...")


def _summary_status(item: dict[str, Any]) -> str:
    if item.get("failed"):
        if item.get("recovered"):
            if int(item.get("recovered") or 0) >= int(item.get("failed") or 0):
                return "partially_failed_recovered"
            return "failed_unrecovered"
        return "failed_unrecovered"
    if item.get("live_calls") or item.get("fake_calls") or item.get("cached_artifacts"):
        return "used"
    if item.get("not_used"):
        return "not_used"
    return "not_used"


def _current_step_payload(event: dict[str, Any], *, index: int, total: int) -> dict[str, Any]:
    return {
        "actor_type": event.get("actor_type"),
        "actor_name": event.get("actor_name"),
        "model_id": event.get("model_id"),
        "action": event.get("action"),
        "stage": event.get("stage"),
        "status": event.get("status"),
        "display_message": trace_display_message(event),
        "step_index": index,
        "step_total": total,
        "started_at": event.get("timestamp"),
        "duration_ms": event.get("duration_ms"),
        "safe_error_message": event.get("safe_error_message"),
    }


def _recent_step_payload(event: dict[str, Any], *, index: int) -> dict[str, Any]:
    return {
        "step_index": index,
        "actor_name": event.get("actor_name"),
        "action": event.get("action"),
        "stage": event.get("stage"),
        "status": event.get("status"),
        "display_message": trace_display_message(event),
        "duration_ms": event.get("duration_ms"),
    }


def _next_step_hint(event: dict[str, Any] | None, *, safe_status: str) -> str | None:
    if safe_status == "succeeded":
        return "結果を表示できます。必要なら snippets を明示的に有効化して根拠を確認してください。"
    if safe_status == "failed":
        return "retrieval-only で候補を確認するか、timeout / model endpoint / 入力範囲を確認してください。"
    if event is None:
        return "Run を押すとローカル検索を開始します。"
    return NEXT_STEP_HINTS.get(str(event.get("stage") or ""))


def _next_fallback_event(events: list[dict[str, Any]], *, start: int) -> dict[str, Any] | None:
    for event in events[start:]:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        if event.get("status") == "fallback_used" or metadata.get("fallback_used"):
            return event
    return None


def _append_unique(values: list[Any], value: Any) -> None:
    if value is None or value == "":
        return
    if value not in values:
        values.append(value)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _safe_metadata(values: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in values.items():
        if value is None or isinstance(value, (str, int, float, bool)):
            safe[str(key)] = value
        elif isinstance(value, (list, tuple)):
            safe[str(key)] = [
                item
                for item in value
                if item is None or isinstance(item, (str, int, float, bool))
            ][:50]
        elif isinstance(value, dict):
            safe[str(key)] = {
                str(inner_key): inner_value
                for inner_key, inner_value in value.items()
                if inner_value is None or isinstance(inner_value, (str, int, float, bool))
            }
    return safe
