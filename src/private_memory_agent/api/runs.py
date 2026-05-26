"""In-memory chat run registry for local UI polling."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock, Thread
from time import perf_counter
from typing import Any

from private_memory_agent.api.contract import build_chat_error_payload, classify_chat_failure
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
    failure_stage: str | None = None
    failure_actor: str | None = None
    result_ready: bool = False
    result_saved_at: str | None = None


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
        public_status = self._public_status(record)
        payload = build_current_status(
            run_id=record.run_id,
            status=public_status,
            events=record.recorder.to_list(),
            elapsed_ms=elapsed,
            warnings=tuple(warnings),
        )
        payload["mode"] = record.options.mode
        payload["result_ready"] = bool(record.result_ready)
        payload["result_available"] = bool(record.result is not None)
        payload["result_saved_at"] = record.result_saved_at
        payload["terminal"] = public_status in {"succeeded", "failed"}
        events = record.recorder.to_list()
        if public_status == "succeeded" and record.result is not None:
            payload["completion_summary"] = _completion_summary(record.result, events=events)
            payload["failure_summary"] = None
            payload["failure_stage"] = None
            payload["failure_actor"] = None
        elif public_status == "failed":
            payload["completion_summary"] = None
            payload["failure_summary"] = _failure_summary(
                record,
                events=events,
                fallback_message=record.safe_error_message,
            )
            payload["failure_stage"] = payload["failure_summary"]["failed_stage"]
            payload["failure_actor"] = payload["failure_summary"]["failed_actor"]
        else:
            payload["completion_summary"] = None
            payload["failure_summary"] = None
            payload["failure_stage"] = None
            payload["failure_actor"] = None
        return payload

    def events(self, run_id: str) -> dict[str, Any]:
        record = self._get(run_id)
        events = record.recorder.to_list()
        return {
            "run_id": record.run_id,
            "status": self._public_status(record),
            "result_ready": bool(record.result_ready),
            "result_available": bool(record.result is not None),
            "result_saved_at": record.result_saved_at,
            "terminal": self._public_status(record) in {"succeeded", "failed"},
            "trace_events": events,
            "model_usage_summary": summarize_model_usage(events),
            "tool_usage_summary": summarize_tool_usage(events),
            "fallback_summary": summarize_fallbacks(events),
        }

    def result(self, run_id: str) -> dict[str, Any]:
        record = self._get(run_id)
        if record.result_ready and record.result is not None:
            return record.result
        public_status = self._public_status(record)
        if record.status == "failed":
            payload = self._failure_result_payload(record)
            with self._lock:
                record.result = payload
                record.result_ready = True
                record.result_saved_at = _timestamp()
            return payload
        if record.status == "succeeded" and not record.result_ready:
            return _not_ready_payload(
                record,
                status="finalizing",
                error_class="ChatRunResultInvariantError",
                message="run completed but final result is still being saved",
            )
        return _not_ready_payload(
            record,
            status=public_status,
            error_class="ChatRunNotReady",
            message="chat run is not complete",
        )

    def _failure_result_payload(self, record: ChatRunRecord) -> dict[str, Any]:
        status_payload = self.status(record.run_id)
        failure = status_payload.get("failure_summary") or {}
        failed_event = next(
            (event for event in reversed(record.recorder.to_list()) if event.get("status") == "failed"),
            {},
        )
        return build_chat_error_payload(
            mode=record.options.mode,
            run_id=record.run_id,
            failure_stage=failure.get("failed_stage") or record.failure_stage or "unknown",
            failure_actor=(
                failure.get("failed_actor")
                or record.failure_actor
                or failed_event.get("actor_name")
                or "ChatRunRegistry"
            ),
            failed_action=failure.get("failed_action") or failed_event.get("action") or "execute_chat_run",
            error_class=(
                failure.get("error_class")
                or record.error_class
                or failed_event.get("error_class")
                or "ChatRunNotReady"
            ),
            error_message=failure.get("safe_error_message")
            or record.safe_error_message
            or "chat run is not complete",
            trace_events=record.recorder.to_list(),
            current_status=status_payload,
            show_answer=record.options.show_answer,
            show_snippets=record.options.show_snippets,
            show_photo_thumbnails=record.options.show_photo_thumbnails,
            show_full_text=record.options.show_full_text,
            show_raw_model_output=record.options.show_raw_model_output,
        )

    def _execute(self, run_id: str) -> None:
        record = self._get(run_id)
        with self._lock:
            record.status = "running"
        try:
            payload = run_chat_console_query(record.options, trace_recorder=record.recorder)
            with self._lock:
                record.status = "finalizing"
                record.result = payload
                record.result_ready = True
                record.result_saved_at = _timestamp()
                record.status = "succeeded"
                record.finished_at_perf = perf_counter()
        except Exception as exc:  # pragma: no cover - defensive safety path
            classification = classify_chat_failure(
                None,
                mode=record.options.mode,
                trace_events=record.recorder.to_list(),
                error_class=exc.__class__.__name__,
                error_message=str(exc),
            )
            failure_stage = classification.get("failure_stage") or "unknown"
            failure_actor = classification.get("failure_actor") or "ChatRunRegistry"
            safe_message = _safe_run_error_message(
                failure_stage=failure_stage,
                failure_actor=failure_actor,
            )
            record.recorder.event(
                actor_type="leader_model" if failure_actor == "DeepSeek Leader" else "tool",
                actor_name=failure_actor,
                stage=failure_stage if failure_stage != "answer_generation" else "answer_synthesis",
                action="execute_chat_run",
                status="failed",
                error_class=exc.__class__.__name__,
                safe_error_message=safe_message,
            )
            with self._lock:
                record.error_class = exc.__class__.__name__
                record.safe_error_message = safe_message
                record.failure_stage = failure_stage
                record.failure_actor = failure_actor
                record.status = "finalizing"
            failure_payload = self._failure_result_payload(record)
            with self._lock:
                record.result = failure_payload
                record.result_ready = True
                record.result_saved_at = _timestamp()
                record.status = "failed"
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

    @staticmethod
    def _public_status(record: ChatRunRecord) -> str:
        if record.status == "succeeded" and not record.result_ready:
            return "finalizing"
        if record.status == "failed" and not record.result_ready:
            return "finalizing"
        return record.status


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
    candidate_dates = temporal.get("candidate_dates") or result.get("candidate_dates") or []
    evidence_refs = answer.get("evidence_references") or []
    evidence_reference_count = int(result.get("evidence_reference_count") or len(evidence_refs))
    warnings = result.get("warnings") or []
    model_summary = result.get("model_usage_summary") or {}
    tool_summary = result.get("tool_usage_summary") or {}
    used_models, unused_models = _used_and_unused_models(model_summary)
    used_tools, unused_tools = _used_and_unused_tools(tool_summary)
    return {
        "summary_status": "done",
        "answer_succeeded": bool(answer.get("answer_succeeded")),
        "answer_state": answer.get("answer_state") or "unknown",
        "candidate_date_count": int(result.get("candidate_date_count") or len(candidate_dates)),
        "evidence_reference_count": evidence_reference_count,
        "evidence_count": int(result.get("evidence_count") or len(result.get("evidence") or [])),
        "evidence_builder_succeeded": bool(result.get("evidence_builder_succeeded")),
        "answer_synthesis_succeeded": bool(result.get("answer_synthesis_succeeded")),
        "used_sources": list(answer.get("used_sources") or []),
        "warning_count": len(warnings),
        "recovered_failure_count": int(result.get("recovered_failure_count") or 0),
        "recovered_failures": list(result.get("recovered_failures") or []),
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
        "failed_stage": record.failure_stage or failed.get("stage") or "unknown",
        "failed_actor": record.failure_actor or failed.get("actor_name") or "ChatRunRegistry",
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
    if payload.get("status") == "partially_failed_recovered":
        return f"{name}: fallback recovered"
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


def _safe_run_error_message(*, failure_stage: str | None, failure_actor: str | None) -> str:
    if failure_stage == "preflight" and failure_actor == "DeepSeek Leader":
        return "leader preflight failed; check pma models ping leader or switch to retrieval-only"
    if failure_stage == "answer_generation" and failure_actor == "DeepSeek Leader":
        return "leader answer generation failed; check timeout or switch to retrieval-only"
    if failure_stage == "answer_validation" and failure_actor == "DeepSeek Leader":
        return "leader answer validation failed; try retrieval-only or reduce answer generation scope"
    return "chat run failed; review safe trace status"


def _not_ready_payload(
    record: ChatRunRecord,
    *,
    status: str,
    error_class: str,
    message: str,
) -> dict[str, Any]:
    status_payload = record_status_payload(
        record,
        status=status,
        message=message,
    )
    payload = build_chat_error_payload(
        mode=record.options.mode,
        run_id=record.run_id,
        failure_stage="unknown",
        failure_actor="ChatRunRegistry",
        failed_action="wait_for_result_handoff",
        error_class=error_class,
        error_message=message,
        trace_events=record.recorder.to_list(),
        current_status=status_payload,
        show_answer=record.options.show_answer,
        show_snippets=record.options.show_snippets,
        show_photo_thumbnails=record.options.show_photo_thumbnails,
        show_full_text=record.options.show_full_text,
        show_raw_model_output=record.options.show_raw_model_output,
    )
    payload["status"] = status
    payload["result_ready"] = False
    payload["result_available"] = False
    payload["result_saved_at"] = None
    payload["terminal"] = False
    payload["failure_stage"] = None
    payload["failure_actor"] = None
    payload["current_status"]["status"] = status
    payload["current_status"]["failure_stage"] = None
    payload["current_status"]["failure_actor"] = None
    payload["current_status"]["failure_summary"] = None
    payload["current_status"]["result_ready"] = False
    payload["current_status"]["result_available"] = False
    payload["current_status"]["terminal"] = False
    payload["warnings"] = [message]
    return payload


def record_status_payload(record: ChatRunRecord, *, status: str, message: str) -> dict[str, Any]:
    payload = build_current_status(
        run_id=record.run_id,
        status=status,
        events=record.recorder.to_list(),
        elapsed_ms=ChatRunRegistry._elapsed_ms(record),
        warnings=(message,),
    )
    payload["mode"] = record.options.mode
    payload["result_ready"] = False
    payload["result_available"] = bool(record.result is not None)
    payload["result_saved_at"] = record.result_saved_at
    payload["terminal"] = False
    payload["failure_summary"] = None
    payload["failure_stage"] = None
    payload["failure_actor"] = None
    return payload


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
