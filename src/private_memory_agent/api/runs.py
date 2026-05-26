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
        return build_current_status(
            run_id=record.run_id,
            status=record.status,
            events=record.recorder.to_list(),
            elapsed_ms=elapsed,
            warnings=tuple(warnings),
        )

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
