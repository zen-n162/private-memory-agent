"""Privacy-safe runtime trace events for local agent execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

TRACE_PRIVACY_LEVEL = "safe_metadata_only"


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
        return [event.to_dict() for event in self._events]

    @property
    def events(self) -> tuple[AgentTraceEvent, ...]:
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
    }


def _summary_status(item: dict[str, Any]) -> str:
    if item.get("failed"):
        return "failed"
    if item.get("live_calls") or item.get("fake_calls") or item.get("cached_artifacts"):
        return "used"
    if item.get("not_used"):
        return "not_used"
    return "not_used"


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
