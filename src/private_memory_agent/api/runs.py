"""In-memory chat run registry for local UI polling."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock, Thread
from time import perf_counter
from typing import Any

from private_memory_agent.api.console import ChatConsoleOptions, run_chat_console_query
from private_memory_agent.tracing import (
    AgentTraceRecorder,
    build_current_status,
    summarize_fallbacks,
    summarize_model_usage,
    summarize_tool_usage,
)


@dataclass
class ChatRunRecord:
    """One local chat run stored in process memory."""

    run_id: str
    options: ChatConsoleOptions
    recorder: AgentTraceRecorder
    status: str = "queued"
    started_at_perf: float = field(default_factory=perf_counter)
    finished_at_perf: float | None = None
    result: dict[str, Any] | None = None
    error_class: str | None = None
    safe_error_message: str | None = None


class ChatRunRegistry:
    """Small process-local background run registry."""

    def __init__(self) -> None:
        self._runs: dict[str, ChatRunRecord] = {}
        self._lock = Lock()

    def start(self, options: ChatConsoleOptions) -> dict[str, Any]:
        recorder = AgentTraceRecorder()
        record = ChatRunRecord(run_id=recorder.run_id, options=options, recorder=recorder)
        recorder.event(
            actor_type="tool",
            actor_name="ChatRunRegistry",
            stage="run_queue",
            action="queue_chat_run",
            status="queued",
            safe_input_summary="local run queued; raw question hidden",
            safe_output_summary="run_id issued for polling",
        )
        with self._lock:
            self._runs[record.run_id] = record
        thread = Thread(target=self._execute, args=(record.run_id,), daemon=True)
        thread.start()
        return self.status(record.run_id)

    def status(self, run_id: str) -> dict[str, Any]:
        record = self._get(run_id)
        warnings: list[str] = []
        if record.safe_error_message:
            warnings.append(record.safe_error_message)
        if record.result:
            warnings.extend(str(item) for item in record.result.get("warnings", []))
        elapsed = self._elapsed_ms(record)
        payload = build_current_status(
            run_id=record.run_id,
            status=record.status,
            events=record.recorder.to_list(),
            elapsed_ms=elapsed,
            warnings=tuple(warnings),
        )
        events = record.recorder.to_list()
        if record.status == "succeeded" and record.result is not None:
            payload["completion_summary"] = _completion_summary(record.result, events=events)
            payload["failure_summary"] = None
        elif record.status == "failed":
            payload["completion_summary"] = None
            payload["failure_summary"] = _failure_summary(
                record,
                events=events,
                fallback_message=record.safe_error_message,
            )
        else:
            payload["completion_summary"] = None
            payload["failure_summary"] = None
        return payload

    def events(self, run_id: str) -> dict[str, Any]:
        record = self._get(run_id)
        events = record.recorder.to_list()
        return {
            "run_id": record.run_id,
            "status": record.status,
            "trace_events": events,
            "model_usage_summary": summarize_model_usage(events),
            "tool_usage_summary": summarize_tool_usage(events),
            "fallback_summary": summarize_fallbacks(events),
        }

    def result(self, run_id: str) -> dict[str, Any]:
        record = self._get(run_id)
        if record.result is not None:
            return record.result
        return {
            "ok": False,
            "run_id": record.run_id,
            "status": record.status,
            "current_status": self.status(run_id),
            "error_class": record.error_class,
            "safe_error_message": record.safe_error_message,
        }

    def _execute(self, run_id: str) -> None:
        record = self._get(run_id)
        with self._lock:
            record.status = "running"
        try:
            payload = run_chat_console_query(record.options, trace_recorder=record.recorder)
            with self._lock:
                record.result = payload
                record.status = "succeeded"
                record.finished_at_perf = perf_counter()
        except Exception as exc:  # pragma: no cover - defensive safety path
            safe_message = "chat run failed; review safe trace status"
            record.recorder.event(
                actor_type="tool",
                actor_name="ChatRunRegistry",
                stage="run_execution",
                action="execute_chat_run",
                status="failed",
                error_class=exc.__class__.__name__,
                safe_error_message=safe_message,
            )
            with self._lock:
                record.status = "failed"
                record.error_class = exc.__class__.__name__
                record.safe_error_message = safe_message
                record.finished_at_perf = perf_counter()

    def _get(self, run_id: str) -> ChatRunRecord:
        with self._lock:
            record = self._runs.get(run_id)
        if record is None:
            raise KeyError(run_id)
        return record

    @staticmethod
    def _elapsed_ms(record: ChatRunRecord) -> int:
        end = record.finished_at_perf if record.finished_at_perf is not None else perf_counter()
        return max(0, int((end - record.started_at_perf) * 1000))


MAJOR_TOOL_NAMES = {
    "DateRangeParserTool",
    "PhotoCoverageDiagnosticsTool",
    "PhotoDateSearchTool",
    "LineNotesDateSearchTool",
    "RetrievalService",
    "EvidenceAcceptanceJudge",
    "EvidenceRelevanceJudge",
    "RetrievalRepair",
    "TemporalAnswerSynthesizer",
}

LOW_LEVEL_TOOL_NAMES = {"AnswerValidator", "PrivacyGuard", "UIResponseRenderer"}


def _completion_summary(result: dict[str, Any], *, events: list[dict[str, Any]]) -> dict[str, Any]:
    answer = result.get("answer") or {}
    temporal = result.get("temporal_event") or {}
    candidate_dates = temporal.get("candidate_dates") or []
    evidence_refs = answer.get("evidence_references") or []
    warnings = result.get("warnings") or []
    model_summary = result.get("model_usage_summary") or {}
    tool_summary = result.get("tool_usage_summary") or {}
    used_models, unused_models = _used_and_unused_models(model_summary)
    used_tools, unused_tools = _used_and_unused_tools(tool_summary)
    return {
        "summary_status": "done",
        "answer_succeeded": bool(answer.get("answer_succeeded")),
        "answer_state": answer.get("answer_state") or "unknown",
        "candidate_date_count": len(candidate_dates),
        "evidence_reference_count": len(evidence_refs),
        "used_sources": list(answer.get("used_sources") or []),
        "warning_count": len(warnings),
        "major_models_used": used_models,
        "major_tools_used": used_tools,
        "unused_models_tools": [*unused_models, *unused_tools],
        "timeline_available": bool(events),
        "display_message": "回答を生成しました" if answer.get("answer_succeeded") else "実行は完了しました",
    }


def _failure_summary(
    record: ChatRunRecord,
    *,
    events: list[dict[str, Any]],
    fallback_message: str | None,
) -> dict[str, Any]:
    failed = next((event for event in reversed(events) if event.get("status") == "failed"), {})
    safe_message = (
        failed.get("safe_error_message")
        or fallback_message
        or "安全なエラー情報を確認してください。"
    )
    return {
        "summary_status": "failed",
        "failed_stage": failed.get("stage") or "unknown",
        "failed_actor": failed.get("actor_name") or "unknown",
        "failed_action": failed.get("action") or "unknown",
        "error_class": failed.get("error_class") or record.error_class,
        "safe_error_message": safe_message,
        "suggested_next_action": (
            "retrieval-only で候補を確認するか、timeout / model endpoint / 入力範囲を確認してください。"
        ),
        "timeline_available": bool(events),
    }


def _used_and_unused_models(summary: dict[str, Any]) -> tuple[list[str], list[str]]:
    used: list[str] = []
    unused: list[str] = []
    for name, payload in summary.items():
        label = _model_usage_label(str(name), payload)
        if _summary_is_used(payload):
            used.append(label)
        else:
            unused.append(label)
    return used, unused


def _used_and_unused_tools(summary: dict[str, Any]) -> tuple[list[str], list[str]]:
    used: list[str] = []
    unused: list[str] = []
    for name, payload in summary.items():
        if name in LOW_LEVEL_TOOL_NAMES or name not in MAJOR_TOOL_NAMES:
            continue
        label = _tool_usage_label(str(name), payload)
        if _tool_is_used(str(name), payload):
            used.append(label)
        else:
            unused.append(label)
    return used, unused


def _summary_is_used(payload: dict[str, Any]) -> bool:
    return bool(
        payload.get("live_calls")
        or payload.get("fake_calls")
        or payload.get("cached_artifacts")
        or payload.get("status") == "used"
    )


def _tool_is_used(name: str, payload: dict[str, Any]) -> bool:
    if name not in MAJOR_TOOL_NAMES:
        return False
    return bool(payload.get("succeeded") or payload.get("fallback_used"))


def _model_usage_label(name: str, payload: dict[str, Any]) -> str:
    if payload.get("live_calls"):
        return f"{name}: live call used"
    if payload.get("fake_calls"):
        return f"{name}: fake model used"
    if payload.get("cached_artifacts"):
        artifacts = ", ".join(payload.get("artifact_types") or ["cached artifact"])
        return f"{name}: cached {artifacts} used"
    return f"{name}: not used"


def _tool_usage_label(name: str, payload: dict[str, Any]) -> str:
    if payload.get("fallback_used"):
        return f"{name}: fallback used"
    if payload.get("succeeded"):
        return f"{name}: used"
    return f"{name}: not used"
