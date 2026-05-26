"""Stable chat API response contract helpers."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from private_memory_agent.tracing import (
    build_current_status,
    summarize_fallbacks,
    summarize_model_usage,
    summarize_tool_usage,
)

CHAT_CONSOLE_MODES = {"retrieval-only", "fake-model", "real-model"}
CHAT_RESPONSE_MODES = {*CHAT_CONSOLE_MODES, "unknown"}
CHAT_API_RESPONSE_SCHEMA_VERSION = "2026-05-26.9h5"
CHAT_UI_RESPONSE_SCHEMA_VERSION = "2026-05-26.9h5"
REQUIRED_CHAT_RESPONSE_KEYS = (
    "ok",
    "mode",
    "answer_succeeded",
    "answer_state",
    "error_class",
    "error_message",
    "failure_stage",
    "failure_actor",
    "current_status",
    "trace_events",
    "trace_summary",
    "privacy",
    "warnings",
    "candidate_dates",
    "evidence",
)

FAILURE_STAGES = {
    "request_validation",
    "config_loading",
    "preflight",
    "query_understanding",
    "retrieval_planning",
    "temporal_parsing",
    "retrieval",
    "evidence_judging",
    "answer_generation",
    "answer_validation",
    "privacy_filtering",
    "ui_response_rendering",
    "unknown",
}


def build_chat_error_payload(
    *,
    mode: str | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
    failure_stage: str = "unknown",
    failure_actor: str = "ChatAPI",
    failed_action: str | None = None,
    error_class: str = "ChatAPIError",
    error_message: str = "request could not be completed",
    trace_events: list[dict[str, Any]] | None = None,
    current_status: dict[str, Any] | None = None,
    elapsed_ms: int = 0,
    warnings: list[str] | tuple[str, ...] = (),
    show_answer: bool = True,
    show_snippets: bool = False,
    show_photo_thumbnails: bool = True,
    show_full_text: bool = False,
    show_raw_model_output: bool = False,
) -> dict[str, Any]:
    """Build a complete privacy-safe chat error payload."""

    safe_run_id = run_id or request_id or str(uuid4())
    safe_stage = _failure_stage(failure_stage)
    safe_message = sanitize_error_message(error_message)
    safe_mode = _response_mode(mode)
    safe_trace_events = list(trace_events or [])
    failure_summary = _failure_summary(
        failure_stage=safe_stage,
        failure_actor=failure_actor,
        failed_action=failed_action or safe_stage,
        error_class=error_class,
        safe_error_message=safe_message,
        timeline_available=bool(safe_trace_events),
    )
    status_payload = current_status or build_current_status(
        run_id=safe_run_id,
        status="failed",
        events=safe_trace_events,
        elapsed_ms=elapsed_ms,
        warnings=tuple(_unique_strings((*warnings, safe_message))),
    )
    status_payload["mode"] = safe_mode
    status_payload["failure_stage"] = safe_stage
    status_payload["failure_actor"] = failure_actor
    status_payload["completion_summary"] = None
    status_payload["failure_summary"] = failure_summary

    answer = {
        "answer_succeeded": False,
        "answer_hidden": not show_answer,
        "answer_state": "not_generated",
        "conclusion": None,
        "confidence": None,
        "unknowns": [],
        "used_sources": [],
        "evidence_references": [],
        "error_class": error_class,
        "error_message": safe_message,
    }
    trace = _default_trace(runtime_event_count=len(safe_trace_events))
    privacy = privacy_defaults(
        show_answer=show_answer,
        show_snippets=show_snippets,
        show_photo_thumbnails=show_photo_thumbnails,
        show_full_text=show_full_text,
        show_raw_model_output=show_raw_model_output,
    )
    payload = {
        "ok": False,
        "request_id": safe_run_id,
        "run_id": safe_run_id,
        "mode": safe_mode,
        "answer_state": "not_generated",
        "answer_succeeded": False,
        "error_class": error_class,
        "error_message": safe_message,
        "failure_stage": safe_stage,
        "failure_actor": failure_actor,
        "current_status": status_payload,
        "answer": answer,
        "evidence": [],
        "evidence_display": {"candidate_dates": [], "evidence_reference_groups": {}},
        "temporal_event": None,
        "candidate_dates": [],
        "trace": trace,
        "trace_events": safe_trace_events,
        "trace_summary": _trace_summary(trace, safe_trace_events),
        "model_usage_summary": summarize_model_usage(safe_trace_events),
        "tool_usage_summary": summarize_tool_usage(safe_trace_events),
        "fallback_summary": summarize_fallbacks(safe_trace_events),
        "privacy": privacy,
        "warnings": _unique_strings((*warnings, safe_message)),
    }
    return payload


def ensure_chat_response_contract(
    payload: dict[str, Any],
    *,
    run_id: str | None = None,
    request_id: str | None = None,
    mode: str | None = None,
    current_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fill stable top-level fields on successful chat payloads."""

    trace_events = list(payload.get("trace_events") or [])
    safe_run_id = run_id or request_id or _first_run_id(trace_events) or str(uuid4())
    answer = payload.get("answer") if isinstance(payload.get("answer"), dict) else {}
    trace = payload.get("trace") if isinstance(payload.get("trace"), dict) else _default_trace()
    privacy = payload.get("privacy") if isinstance(payload.get("privacy"), dict) else privacy_defaults()
    safe_mode = _response_mode(mode or payload.get("mode"))
    failure = classify_chat_failure(
        payload,
        mode=safe_mode,
        trace_events=trace_events,
    )
    failure_stage = payload.get("failure_stage") or failure.get("failure_stage")
    failure_actor = payload.get("failure_actor") or failure.get("failure_actor")
    error_class = answer.get("error_class") or payload.get("error_class")
    error_message = answer.get("error_message") or payload.get("error_message")
    if error_message:
        error_message = sanitize_error_message(str(error_message))

    payload["request_id"] = payload.get("request_id") or safe_run_id
    payload["run_id"] = payload.get("run_id") or safe_run_id
    payload["mode"] = safe_mode
    payload["answer_state"] = answer.get("answer_state") or "not_generated"
    payload["answer_succeeded"] = bool(answer.get("answer_succeeded"))
    payload["error_class"] = error_class
    payload["error_message"] = error_message
    payload["failure_stage"] = _failure_stage(failure_stage) if failure_stage else None
    payload["failure_actor"] = failure_actor
    payload["candidate_dates"] = _candidate_dates(payload)
    payload["trace_summary"] = _trace_summary(trace, trace_events)
    payload["privacy"] = {**privacy_defaults(), **privacy}
    payload["warnings"] = list(payload.get("warnings") or [])
    if current_status is not None:
        payload["current_status"] = current_status
    else:
        status = "failed" if payload.get("failure_stage") else "succeeded"
        payload["current_status"] = build_current_status(
            run_id=safe_run_id,
            status=status,
            events=trace_events,
            warnings=tuple(payload["warnings"]),
        )
    if payload.get("failure_stage"):
        failed_action = failure.get("failed_action") or str(payload.get("failure_stage"))
        safe_actor = str(payload.get("failure_actor") or "ChatAPI")
        safe_error_class = str(payload.get("error_class") or "ChatAPIError")
        safe_error_message = str(payload.get("error_message") or "request could not be completed")
        payload["current_status"]["status"] = "failed"
        payload["current_status"]["failure_stage"] = payload["failure_stage"]
        payload["current_status"]["failure_actor"] = safe_actor
        payload["current_status"]["completion_summary"] = None
        payload["current_status"]["failure_summary"] = _failure_summary(
            failure_stage=str(payload["failure_stage"]),
            failure_actor=safe_actor,
            failed_action=failed_action,
            error_class=safe_error_class,
            safe_error_message=safe_error_message,
            timeline_available=bool(trace_events),
        )
    return payload


def classify_chat_failure(
    payload: dict[str, Any] | None,
    *,
    mode: str | None = None,
    trace_events: list[dict[str, Any]] | None = None,
    error_class: str | None = None,
    error_message: str | None = None,
) -> dict[str, str | None]:
    """Classify a chat failure into stable UI/API stage metadata."""

    safe_payload = payload or {}
    answer = safe_payload.get("answer") if isinstance(safe_payload.get("answer"), dict) else {}
    events = list(trace_events if trace_events is not None else safe_payload.get("trace_events") or [])
    failed = next((event for event in reversed(events) if event.get("status") == "failed"), {})
    safe_mode = _response_mode(mode or safe_payload.get("mode"))
    safe_error_class = (
        error_class
        or answer.get("error_class")
        or safe_payload.get("error_class")
        or failed.get("error_class")
    )
    safe_error_message = (
        error_message
        or answer.get("error_message")
        or safe_payload.get("error_message")
        or failed.get("safe_error_message")
    )
    existing_stage = safe_payload.get("failure_stage")
    if existing_stage:
        stage = _failure_stage(str(existing_stage))
    else:
        stage = _stage_from_failed_event(failed)
        if stage == "unknown":
            stage = _stage_from_error(safe_error_class, safe_error_message, mode=safe_mode)
    actor = (
        safe_payload.get("failure_actor")
        or failed.get("actor_name")
        or _actor_for_stage(stage, mode=safe_mode)
    )
    action = failed.get("action") or _action_for_stage(stage)
    if not (safe_error_class or existing_stage or failed):
        return {
            "failure_stage": None,
            "failure_actor": None,
            "failed_action": None,
        }
    return {
        "failure_stage": stage,
        "failure_actor": str(actor),
        "failed_action": str(action),
    }


def privacy_defaults(
    *,
    show_answer: bool = True,
    show_snippets: bool = False,
    show_photo_thumbnails: bool = True,
    show_full_text: bool = False,
    show_raw_model_output: bool = False,
) -> dict[str, Any]:
    """Return local-only chat privacy defaults."""

    return {
        "local_only": True,
        "snippets_hidden": not show_snippets,
        "answer_hidden": not show_answer,
        "photo_thumbnails_hidden": not show_photo_thumbnails,
        "full_text_hidden": not show_full_text,
        "raw_model_output_hidden": not show_raw_model_output,
        "external_network_disabled": True,
        "raw_private_content_returned": False,
        "warnings": [],
    }


def sanitize_error_message(message: str) -> str:
    """Remove path-like details from safe error messages."""

    lowered = message.lower()
    if "path" in lowered or "/" in message or "\\" in message:
        return "request could not be completed"
    return message


def _candidate_dates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    display = payload.get("evidence_display") if isinstance(payload.get("evidence_display"), dict) else {}
    temporal = payload.get("temporal_event") if isinstance(payload.get("temporal_event"), dict) else {}
    candidates = display.get("candidate_dates") or temporal.get("candidate_dates") or []
    return list(candidates) if isinstance(candidates, list) else []


def _default_trace(*, runtime_event_count: int = 0) -> dict[str, Any]:
    return {
        "runtime_event_count": runtime_event_count,
        "plan_created": False,
        "plan": {
            "plan_created": False,
            "main_entity_count": 0,
            "specific_concept_count": 0,
            "generic_concept_count": 0,
            "retrieval_query_count": 0,
        },
        "semantic_candidate_count": 0,
        "reranked_candidate_count": 0,
        "repair_attempted": False,
        "repair_improved": False,
        "usable_evidence_succeeded": False,
        "usable_evidence_count": 0,
        "final_relevance_score": None,
        "insufficient_evidence_reason": "execution stopped before agent runtime",
    }


def _trace_summary(trace: dict[str, Any], trace_events: list[dict[str, Any]]) -> dict[str, Any]:
    plan = trace.get("plan") if isinstance(trace.get("plan"), dict) else {}
    return {
        "runtime_event_count": trace.get("runtime_event_count", len(trace_events)),
        "plan_created": bool(trace.get("plan_created") or plan.get("plan_created")),
        "main_entity_count": plan.get("main_entity_count", 0),
        "specific_concept_count": plan.get("specific_concept_count", 0),
        "generic_concept_count": plan.get("generic_concept_count", 0),
        "semantic_candidate_count": trace.get("semantic_candidate_count", 0),
        "final_relevance_score": trace.get("final_relevance_score"),
    }


def _failure_summary(
    *,
    failure_stage: str,
    failure_actor: str,
    failed_action: str,
    error_class: str,
    safe_error_message: str,
    timeline_available: bool,
) -> dict[str, Any]:
    return {
        "summary_status": "failed",
        "failed_stage": failure_stage,
        "failed_actor": failure_actor,
        "failed_action": failed_action,
        "error_class": error_class,
        "safe_error_message": safe_error_message,
        "suggested_next_action": _suggested_next_action(failure_stage),
        "timeline_available": timeline_available,
    }


def _suggested_next_action(failure_stage: str) -> str:
    if failure_stage == "request_validation":
        return "入力形式と mode / option の値を確認してください。"
    if failure_stage == "preflight":
        return "Check pma models ping leader or switch to retrieval-only."
    if failure_stage == "answer_generation":
        return "retrieval-only で候補を確認するか、timeout を増やしてください。"
    return "retrieval-only で候補を確認し、設定と safe trace を確認してください。"


def _failure_stage(value: str | None) -> str:
    if value in FAILURE_STAGES:
        return str(value)
    return "unknown"


def _stage_from_failed_event(event: dict[str, Any]) -> str:
    stage = str(event.get("stage") or "")
    action = str(event.get("action") or "")
    if stage in {"leader_endpoint_preflight", "configuration"}:
        return "preflight"
    if action in {"preflight_event_intent_planner"}:
        return "preflight"
    if stage == "answer_synthesis" or action == "generate_structured_answer":
        return "answer_generation"
    if stage == "answer_validation" or action == "validate_answer_payload":
        return "answer_validation"
    if stage in {"evidence_retrieval", "evidence_retrieval_repair"}:
        return "retrieval"
    if "retrieval_planning" in stage:
        return "retrieval_planning"
    return _failure_stage(stage)


def _stage_from_error(
    error_class: str | None,
    error_message: str | None,
    *,
    mode: str,
) -> str:
    text = f"{error_class or ''} {error_message or ''}".lower()
    if "answervalidationerror" in text:
        return "answer_validation"
    if mode == "real-model" and (
        "configured leader" in text
        or "model key" in text
        or "endpoint_url" in text
        or "leader model" in text
    ):
        return "preflight"
    if "endpoint" in text and ("unavailable" in text or "preflight" in text or "models" in text):
        return "preflight"
    if mode == "real-model" and (error_class or "modelruntimeerror" in text):
        return "answer_generation"
    if error_class:
        return "unknown"
    return "unknown"


def _actor_for_stage(stage: str | None, *, mode: str) -> str:
    if mode == "real-model" and stage in {"preflight", "answer_generation", "answer_validation"}:
        return "DeepSeek Leader"
    if stage == "request_validation":
        return "ChatAPI"
    return "Chat runtime"


def _action_for_stage(stage: str | None) -> str:
    if stage == "preflight":
        return "preflight_leader_model"
    if stage == "answer_generation":
        return "generate_structured_answer"
    if stage == "answer_validation":
        return "validate_answer_payload"
    if stage == "request_validation":
        return "validate_chat_request"
    return str(stage or "unknown")


def _response_mode(value: str | None) -> str:
    if value in CHAT_RESPONSE_MODES:
        return str(value)
    return "unknown"


def _first_run_id(trace_events: list[dict[str, Any]]) -> str | None:
    for event in trace_events:
        run_id = event.get("run_id")
        if run_id:
            return str(run_id)
    return None


def _unique_strings(items: tuple[Any, ...] | list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result
