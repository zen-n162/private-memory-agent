"""Evidence-first local chat console service.

The console service is intentionally a thin adapter over the existing E2E
retrieval/answer pipeline. It returns UI-friendly metadata, not raw private
payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from private_memory_agent.agent import (
    DeterministicRuleBasedRetrievalPlanner,
    LeaderRetrievalPlanner,
    RetrievalPlan,
)
from private_memory_agent.agent.retrieval_planner import plan_metadata_for_error
from private_memory_agent.api.evidence_view import (
    EvidenceDisplayOptions,
    build_evidence_display_payload,
)
from private_memory_agent.config import ConfigBundle, load_config
from private_memory_agent.e2e import (
    DEFAULT_E2E_REAL_MODEL_MAX_TOKENS,
    DEFAULT_E2E_REAL_MODEL_TIMEOUT_SECONDS,
    E2ESmokeOptions,
    E2ESmokeQuery,
    E2ESmokeQueryResult,
    E2ESmokeReport,
    run_e2e_smoke,
)
from private_memory_agent.runtime import (
    ModelRuntimeError,
    OpenAICompatibleHTTPClient,
    configured_model_endpoints,
    endpoint_from_model_spec,
    preflight_chat_endpoint,
)
from private_memory_agent.temporal import answer_temporal_event_query

ConsoleMode = Literal["retrieval-only", "fake-model", "real-model"]
ConsoleSource = Literal["photos", "line", "notes"]
ConsoleSemanticModel = Literal[
    "none",
    "hash",
    "fake",
    "ruri-v3-310m",
    "ruri-v3-130m",
    "bge-m3",
    "qwen3-embedding-0.6b",
]
ConsoleReranker = Literal[
    "none",
    "fake",
    "ruri-v3-reranker-310m",
    "qwen3-reranker-0.6b",
]

SUPPORTED_CONSOLE_SOURCES = {"photos", "line", "notes"}


@dataclass(frozen=True)
class ChatConsoleOptions:
    """Options accepted by the local chat console service."""

    question: str
    config_dir: Path | str | None = None
    paths_config: Path | str | None = None
    db_path: Path | str = Path("data/local/private_memory_agent.sqlite3")
    mode: ConsoleMode = "retrieval-only"
    sources: tuple[str, ...] = ()
    leader_plan: bool = True
    leader_rerank: bool = True
    semantic: bool = False
    semantic_model: str = "hash"
    semantic_top_k: int | None = 20
    semantic_weight: float = 1.0
    reranker: str = "none"
    rerank_top_k: int | None = 20
    retrieval_repair: int = 1
    strict_relevance: bool = False
    minimum_relevance_score: float = 0.6
    show_answer: bool = True
    show_snippets: bool = False
    show_photo_thumbnails: bool = True
    show_full_text: bool = False
    show_raw_model_output: bool = False
    snippet_chars: int = 160
    limit: int = 5
    temporal_top_candidate_dates: int = 10
    temporal_top_evidence_per_date: int = 5
    timeout_seconds: float | None = None
    max_tokens: int = DEFAULT_E2E_REAL_MODEL_MAX_TOKENS
    model_key: str = "leader"
    embedding_device: str = "auto"
    allow_remote: bool = False


def run_chat_console_query(options: ChatConsoleOptions) -> dict[str, Any]:
    """Run one privacy-safe console query."""

    _validate_options(options)
    temporal_payload = _maybe_temporal_console_payload(options)
    if temporal_payload is not None:
        return temporal_payload
    config = load_config(config_dir=options.config_dir, paths_config=options.paths_config)
    plan, plan_metadata, plan_warning = _build_plan(options, config)
    query = _build_console_e2e_query(options, plan=plan, repair=False)
    report = _run_console_e2e(options, query=query)
    result = report.query_results[0] if report.query_results else _empty_result()
    repair_status = _repair_status(attempted=False)
    warnings = [*report.warnings]
    if plan_warning:
        warnings.append(plan_warning)

    if _needs_repair(result, options) and options.retrieval_repair > 0 and plan is not None:
        repair_query = _build_console_e2e_query(options, plan=plan, repair=True)
        repair_report = _run_console_e2e(options, query=repair_query)
        repaired = repair_report.query_results[0] if repair_report.query_results else result
        pre_usable = _usable_count(result, options.minimum_relevance_score)
        post_usable = _usable_count(repaired, options.minimum_relevance_score)
        improved = post_usable > pre_usable or (
            post_usable == pre_usable and repaired.evidence_count > result.evidence_count
        )
        repair_status = _repair_status(
            attempted=True,
            count=1,
            improved=improved,
            reason=_insufficient_reason(result, options),
            pre_usable=pre_usable,
            post_usable=post_usable,
            repair_query_count=len(plan.retrieval_queries),
            repair_queries_created_count=len(_repair_terms(plan)),
            repair_specific_query_count=len(_repair_terms(plan)),
            repair_generic_query_count=0,
            repair_used_specific_concepts=bool(plan.specific_concepts),
            repair_used_main_entities=bool(plan.main_entities),
        )
        warnings.extend(repair_report.warnings)
        if improved:
            report = repair_report
            result = repaired

    relevance_policy_passed = _relevance_policy_passed(result, options)
    ok = bool(report.db_exists and result.retrieval_succeeded and relevance_policy_passed)
    if options.mode in {"fake-model", "real-model"}:
        ok = ok and bool(result.answer_succeeded)

    privacy = _privacy_payload(options)
    answer = _answer_payload(result, show_answer=options.show_answer)
    evidence = _evidence_payload(result, show_snippets=options.show_snippets)
    return {
        "ok": ok,
        "mode": options.mode,
        "answer": answer,
        "evidence": evidence,
        "evidence_display": build_evidence_display_payload(
            options.db_path,
            evidence=evidence,
            answer_evidence_references=answer["evidence_references"],
            options=_evidence_display_options(options),
        ),
        "trace": _trace_payload(
            result,
            report=report,
            plan_metadata=plan_metadata,
            repair_status=repair_status,
            minimum_relevance_score=options.minimum_relevance_score,
            strict_relevance=options.strict_relevance,
        ),
        "privacy": privacy,
        "warnings": _unique_strings((*warnings, *privacy["warnings"])),
    }


def build_system_status(
    *,
    config_dir: Path | str | None = None,
    paths_config: Path | str | None = None,
    db_path: Path | str = Path("data/local/private_memory_agent.sqlite3"),
) -> dict[str, Any]:
    """Return cheap, privacy-safe system status for the console."""

    config = load_config(config_dir=config_dir, paths_config=paths_config)
    db = Path(db_path).expanduser()
    counts: dict[str, Any] = {}
    indexes: dict[str, Any] = {}
    warnings: list[str] = []
    if db.exists():
        report = run_e2e_smoke(
            E2ESmokeOptions(
                config_dir=config_dir,
                paths_config=paths_config,
                db_path=db,
                dry_run=True,
            ),
        )
        counts = report.counts.to_dict()
        indexes = {
            "text_documents_count": report.indexes.text_documents_count,
            "text_index_available": report.indexes.text_index_available,
            "text_fts_available": report.indexes.text_fts_available,
            "embeddings_count": report.indexes.embeddings_count,
            "embedding_model_breakdown": report.indexes.embedding_model_breakdown,
            "media_annotations_in_text_index_count": (
                report.indexes.media_annotations_in_text_index_count
            ),
        }
        warnings.extend(report.warnings)
    return {
        "ok": True,
        "localhost_only": True,
        "db_exists": db.exists(),
        "counts": counts,
        "indexes": indexes,
        "models": _model_status(config),
        "privacy": {
            "local_only": True,
            "raw_private_content_returned": False,
            "raw_model_output_hidden": True,
        },
        "warnings": tuple(warnings),
    }


def _validate_options(options: ChatConsoleOptions) -> None:
    if not str(options.question).strip():
        raise ValueError("question is required")
    if options.mode not in {"retrieval-only", "fake-model", "real-model"}:
        raise ValueError("mode must be retrieval-only, fake-model, or real-model")
    unknown_sources = set(options.sources) - SUPPORTED_CONSOLE_SOURCES
    if unknown_sources:
        raise ValueError(f"unsupported sources: {sorted(unknown_sources)}")
    if options.limit <= 0 or options.limit > 20:
        raise ValueError("limit must be between 1 and 20")
    if options.retrieval_repair < 0 or options.retrieval_repair > 3:
        raise ValueError("retrieval_repair must be between 0 and 3")
    if options.minimum_relevance_score < 0 or options.minimum_relevance_score > 1:
        raise ValueError("minimum_relevance_score must be between 0.0 and 1.0")
    if options.semantic_top_k is not None and options.semantic_top_k <= 0:
        raise ValueError("semantic_top_k must be positive")
    if options.rerank_top_k is not None and options.rerank_top_k <= 0:
        raise ValueError("rerank_top_k must be positive")
    if options.max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if options.timeout_seconds is not None and options.timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if options.snippet_chars <= 0:
        raise ValueError("snippet_chars must be positive")
    if options.temporal_top_candidate_dates <= 0 or options.temporal_top_candidate_dates > 50:
        raise ValueError("temporal_top_candidate_dates must be between 1 and 50")
    if options.temporal_top_evidence_per_date <= 0 or options.temporal_top_evidence_per_date > 20:
        raise ValueError("temporal_top_evidence_per_date must be between 1 and 20")


def _maybe_temporal_console_payload(options: ChatConsoleOptions) -> dict[str, Any] | None:
    if options.sources and "photos" not in options.sources:
        return None
    result = answer_temporal_event_query(
        options.question,
        db_path=options.db_path,
        top_days=options.limit,
        top_candidate_dates=options.temporal_top_candidate_dates,
        top_evidence_per_date=options.temporal_top_evidence_per_date,
    )
    if result is None:
        return None
    privacy = _privacy_payload(options)
    answer = result.answer.to_dict(show_answer=options.show_answer)
    temporal_event = result.to_dict(show_answer=options.show_answer)
    evidence = [item.to_dict() for item in result.evidence]
    warnings = _unique_strings((*result.warnings, *privacy["warnings"]))
    should_use_count = len(result.answer.evidence_references)
    return {
        "ok": result.ok,
        "mode": options.mode,
        "answer": answer,
        "evidence": evidence,
        "evidence_display": build_evidence_display_payload(
            options.db_path,
            evidence=evidence,
            answer_evidence_references=answer["evidence_references"],
            candidate_dates=temporal_event["candidate_dates"],
            options=_evidence_display_options(options),
        ),
        "temporal_event": temporal_event,
        "trace": {
            "query_type": result.query.query_type,
            "temporal_event": True,
            "plan_created": False,
            "plan": {
                "plan_created": False,
                "main_entity_count": 0,
                "specific_concept_count": 0,
                "generic_concept_count": 0,
                "retrieval_query_count": 0,
            },
            "source_coverage": {},
            "evidence_source_counts": _evidence_source_counts(result.answer.evidence_references),
            "semantic_candidate_count": 0,
            "reranked_candidate_count": 0,
            "retrieval_stage_counts": result.diagnostics,
            "temporal_diagnostics": result.diagnostics,
            "repair_attempted": False,
            "repair_improved": False,
            "retrieval_repair_count": 0,
            "repair_reason": None,
            "usable_evidence_succeeded": should_use_count > 0,
            "usable_evidence_count": should_use_count,
            "unusable_evidence_count": max(0, len(result.evidence) - should_use_count),
            "should_use_evidence_count": should_use_count,
            "average_plan_relevance_score": None,
            "final_relevance_score": result.answer.confidence,
            "relevance_policy": "strict" if options.strict_relevance else "soft",
            "relevance_policy_passed": True if not options.strict_relevance else should_use_count > 0,
            "minimum_relevance_score": options.minimum_relevance_score,
            "plan_relevance_specificity_counts": {},
            "insufficient_evidence_reason": _temporal_insufficient_reason(
                result.diagnostics,
                should_use_count=should_use_count,
            ),
            "json_extraction_succeeded": None,
            "json_extraction_strategy": "not_applicable_temporal_event",
            "json_retry_used": False,
            "json_retry_succeeded": False,
            "candidate_dates": [item.to_dict() for item in result.candidate_dates],
            "warnings": list(result.warnings),
        },
        "privacy": privacy,
        "warnings": list(warnings),
    }


def _build_plan(
    options: ChatConsoleOptions,
    config: ConfigBundle,
) -> tuple[RetrievalPlan | None, dict[str, Any], str | None]:
    if not options.leader_plan:
        return None, {"plan_created": False}, None
    try:
        planner = (
            _real_leader_planner(options, config)
            if options.mode == "real-model"
            else DeterministicRuleBasedRetrievalPlanner()
        )
        plan = planner.plan(options.question)
        return plan, plan.metadata(show_plan=False).to_dict(), None
    except (ModelRuntimeError, RuntimeError, ValueError) as exc:
        metadata = plan_metadata_for_error(exc).to_dict()
        return None, metadata, "retrieval planning failed; deterministic query path was used"


def _real_leader_planner(
    options: ChatConsoleOptions,
    config: ConfigBundle,
) -> LeaderRetrievalPlanner:
    model_spec = config.model_registry.get(options.model_key)
    if model_spec is None:
        raise ValueError("configured leader model key was not found")
    endpoint = endpoint_from_model_spec(model_spec)
    if endpoint is None:
        raise ValueError("configured leader model endpoint_url is missing")
    preflight = preflight_chat_endpoint(endpoint, allow_remote=options.allow_remote)
    timeout_seconds = (
        options.timeout_seconds
        if options.timeout_seconds is not None
        else endpoint.request_timeout_seconds or DEFAULT_E2E_REAL_MODEL_TIMEOUT_SECONDS
    )
    client = OpenAICompatibleHTTPClient(
        base_url=endpoint.base_url,
        model=preflight.served_model_name,
        timeout_seconds=timeout_seconds,
        retries=endpoint.retries,
        allow_remote=options.allow_remote,
    )
    return LeaderRetrievalPlanner(
        client,
        model=preflight.served_model_name,
        max_tokens=min(max(128, options.max_tokens), 1024),
        temperature=0.0,
    )


def _build_console_e2e_query(
    options: ChatConsoleOptions,
    *,
    plan: RetrievalPlan | None,
    repair: bool,
) -> E2ESmokeQuery:
    requested_sources = options.sources or (plan.source_constraints if plan else ())
    if not requested_sources and plan is not None:
        requested_sources = plan.source_preferences
    retrieval_text = _retrieval_text(options.question, plan=plan, repair=repair)
    return E2ESmokeQuery(
        query_id="ui_query",
        text=options.question,
        sources=tuple(requested_sources),
        retrieval_text=retrieval_text,
        preferred_sources=plan.source_preferences if plan is not None else (),
        retrieval_plan=plan,
        judge_relevance=bool(plan and options.leader_rerank),
        rerank_by_relevance=bool(plan and options.leader_rerank),
        show_relevance=bool(plan and options.leader_rerank),
        semantic_enabled=options.semantic,
        semantic_top_k=options.semantic_top_k,
        semantic_weight=options.semantic_weight,
        reranker=options.reranker,
        rerank_top_k=options.rerank_top_k,
    )


def _run_console_e2e(options: ChatConsoleOptions, *, query: E2ESmokeQuery) -> E2ESmokeReport:
    semantic_model = options.semantic_model if options.semantic else "none"
    return run_e2e_smoke(
        E2ESmokeOptions(
            config_dir=options.config_dir,
            paths_config=options.paths_config,
            db_path=options.db_path,
            queries=(query,),
            retrieval_only=options.mode == "retrieval-only",
            fake_model=options.mode == "fake-model",
            real_model=options.mode == "real-model",
            no_fallback=True,
            diagnose=True,
            limit=options.limit,
            timeout_seconds=options.timeout_seconds,
            max_tokens=options.max_tokens,
            show_answer=options.show_answer,
            show_snippets=options.show_snippets,
            snippet_chars=options.snippet_chars,
            semantic_model=semantic_model,
            semantic_top_k=options.semantic_top_k,
            semantic_weight=options.semantic_weight,
            reranker=options.reranker,
            rerank_top_k=options.rerank_top_k,
            embedding_device=options.embedding_device,
            model_key=options.model_key,
            allow_remote=options.allow_remote,
        ),
    )


def _retrieval_text(question: str, *, plan: RetrievalPlan | None, repair: bool) -> str:
    if plan is None:
        return question
    if repair:
        terms = _repair_terms(plan)
        return " ".join(terms) if terms else question
    query_terms = _unique_strings(
        (
            *plan.retrieval_queries,
            *plan.specific_concepts,
            *plan.main_entities,
        ),
    )
    return " ".join(query_terms) if query_terms else question


def _repair_terms(plan: RetrievalPlan) -> tuple[str, ...]:
    return _unique_strings((*plan.specific_concepts, *plan.main_entities, *plan.retrieval_queries))


def _needs_repair(result: E2ESmokeQueryResult, options: ChatConsoleOptions) -> bool:
    if result.evidence_count == 0:
        return True
    if result.plan_relevance_judged and _usable_count(result, options.minimum_relevance_score) == 0:
        return True
    return False


def _usable_count(result: E2ESmokeQueryResult, minimum_relevance_score: float) -> int:
    if result.plan_relevance_scores:
        return sum(
            1
            for score in result.plan_relevance_scores
            if bool(score.get("should_use"))
            and float(score.get("relevance_score") or 0.0) >= minimum_relevance_score
        )
    return result.evidence_count if result.retrieval_succeeded else 0


def _relevance_policy_passed(result: E2ESmokeQueryResult, options: ChatConsoleOptions) -> bool:
    if not options.strict_relevance:
        return True
    return _usable_count(result, options.minimum_relevance_score) > 0


def _answer_payload(result: E2ESmokeQueryResult, *, show_answer: bool) -> dict[str, Any]:
    state = _answer_state(result, show_answer=show_answer)
    return {
        "answer_succeeded": result.answer_succeeded,
        "answer_hidden": state == "hidden",
        "answer_state": state,
        "conclusion": result.answer_conclusion if show_answer else None,
        "confidence": result.answer_confidence,
        "unknowns": list(result.answer_unknowns) if show_answer else [],
        "used_sources": list(result.used_sources),
        "evidence_references": list(result.answer_evidence_references),
        "error_class": result.error_class,
        "error_message": result.error_message,
    }


def _answer_state(result: E2ESmokeQueryResult, *, show_answer: bool) -> str:
    if not result.answer_succeeded:
        return "not_generated"
    if not show_answer:
        return "hidden"
    conclusion = str(result.answer_conclusion or "").strip()
    if result.answer_confidence == 0.0:
        return "unknown"
    lowered = conclusion.lower()
    if any(token in lowered for token in ("unknown", "insufficient", "not enough")):
        return "unknown"
    if any(token in conclusion for token in ("不明", "不十分", "足りない")):
        return "unknown"
    return "visible"


def _evidence_payload(
    result: E2ESmokeQueryResult,
    *,
    show_snippets: bool,
) -> list[dict[str, Any]]:
    scores = {str(score.get("evidence_id")): score for score in result.plan_relevance_scores}
    snippets = {
        str(item.get("evidence_id")): item
        for item in result.safe_snippets
        if item.get("evidence_id")
    }
    used_refs = set(result.answer_evidence_references)
    items: list[dict[str, Any]] = []
    for evidence_id in result.evidence_ids:
        score = scores.get(evidence_id, {})
        item = {
            "evidence_id": evidence_id,
            "source_type": _source_from_evidence_id(evidence_id),
            "should_use": score.get("should_use"),
            "specificity": score.get("specificity"),
            "relevance_score": score.get("relevance_score"),
            "reason_category": score.get("reason_category"),
            "used_by_answer": evidence_id in used_refs,
        }
        if show_snippets and evidence_id in snippets:
            item["snippet"] = snippets[evidence_id].get("snippet")
        items.append(item)
    return items


def _trace_payload(
    result: E2ESmokeQueryResult,
    *,
    report: E2ESmokeReport,
    plan_metadata: dict[str, Any],
    repair_status: dict[str, Any],
    minimum_relevance_score: float,
    strict_relevance: bool,
) -> dict[str, Any]:
    should_use_count = _usable_count(result, minimum_relevance_score)
    insufficient_reason = _insufficient_reason(result, ChatConsoleOptions(question="_"))
    return {
        "plan_created": bool(plan_metadata.get("plan_created")),
        "plan": plan_metadata,
        "source_coverage": report.source_coverage.to_dict(),
        "evidence_source_counts": dict(result.evidence_source_counts),
        "semantic_candidate_count": result.semantic_candidate_count,
        "reranked_candidate_count": result.reranked_candidate_count,
        "retrieval_stage_counts": dict(result.retrieval_stage_counts),
        "repair_attempted": repair_status["repair_attempted"],
        "repair_improved": repair_status["repair_improved"],
        "retrieval_repair_count": repair_status["retrieval_repair_count"],
        "repair_reason": repair_status["repair_reason"],
        "usable_evidence_succeeded": should_use_count > 0,
        "usable_evidence_count": should_use_count,
        "unusable_evidence_count": max(0, result.evidence_count - should_use_count),
        "should_use_evidence_count": should_use_count,
        "average_plan_relevance_score": result.plan_average_relevance_score,
        "final_relevance_score": result.plan_average_relevance_score,
        "relevance_policy": "strict" if strict_relevance else "soft",
        "relevance_policy_passed": True if not strict_relevance else should_use_count > 0,
        "minimum_relevance_score": minimum_relevance_score,
        "plan_relevance_specificity_counts": dict(result.plan_relevance_specificity_counts),
        "insufficient_evidence_reason": insufficient_reason,
        "json_extraction_succeeded": result.json_extraction_succeeded,
        "json_extraction_strategy": result.json_extraction_strategy,
        "json_retry_used": result.json_retry_used,
        "json_retry_succeeded": result.json_retry_succeeded,
        "warnings": list(report.warnings),
    }


def _privacy_payload(options: ChatConsoleOptions) -> dict[str, Any]:
    warnings = []
    if options.show_snippets:
        warnings.append("show_snippets is enabled; snippets may contain private local content")
    if options.show_full_text:
        warnings.append("show_full_text is enabled; local evidence text may be less truncated")
    if options.show_raw_model_output:
        warnings.append("show_raw_model_output is requested, but raw model output is not returned by this API")
    if options.show_answer:
        warnings.append("show_answer is enabled; answer text may contain private local content")
    return {
        "local_only": True,
        "snippets_hidden": not options.show_snippets,
        "photo_thumbnails_hidden": not options.show_photo_thumbnails,
        "full_text_hidden": not options.show_full_text,
        "answer_hidden": not options.show_answer,
        "raw_model_output_hidden": True,
        "external_network_disabled": not options.allow_remote,
        "raw_private_content_hidden_by_default": True,
        "paths_hidden": True,
        "gps_hidden": True,
        "exif_hidden": True,
        "warnings": warnings,
    }


def _evidence_display_options(options: ChatConsoleOptions) -> EvidenceDisplayOptions:
    return EvidenceDisplayOptions(
        show_snippets=options.show_snippets,
        show_photo_thumbnails=options.show_photo_thumbnails,
        show_full_text=options.show_full_text,
        snippet_chars=options.snippet_chars,
    )


def _repair_status(
    *,
    attempted: bool,
    count: int = 0,
    improved: bool = False,
    reason: str | None = None,
    pre_usable: int = 0,
    post_usable: int = 0,
    repair_query_count: int = 0,
    repair_queries_created_count: int = 0,
    repair_specific_query_count: int = 0,
    repair_generic_query_count: int = 0,
    repair_used_specific_concepts: bool = False,
    repair_used_main_entities: bool = False,
) -> dict[str, Any]:
    return {
        "repair_attempted": attempted,
        "retrieval_repair_count": count,
        "repair_improved": improved,
        "repair_reason": reason,
        "pre_repair_usable_evidence_count": pre_usable,
        "post_repair_usable_evidence_count": post_usable,
        "repair_query_count": repair_query_count,
        "repair_queries_created_count": repair_queries_created_count,
        "repair_specific_query_count": repair_specific_query_count,
        "repair_generic_query_count": repair_generic_query_count,
        "repair_used_specific_concepts": repair_used_specific_concepts,
        "repair_used_main_entities": repair_used_main_entities,
    }


def _empty_result() -> E2ESmokeQueryResult:
    return E2ESmokeQueryResult(
        query_label="query_1",
        sources=(),
        retrieval_succeeded=False,
        evidence_count=0,
    )


def _insufficient_reason(
    result: E2ESmokeQueryResult,
    options: ChatConsoleOptions,
) -> str | None:
    if result.evidence_count == 0:
        return "no candidate evidence was retrieved"
    if result.plan_relevance_judged and _usable_count(result, options.minimum_relevance_score) == 0:
        return "candidate evidence was found, but relevance judge found no usable evidence"
    return None


def _source_from_evidence_id(evidence_id: str) -> str:
    if evidence_id.startswith("media_items:") or evidence_id.startswith("media_annotations:"):
        return "photos"
    if evidence_id.startswith("line_messages:"):
        return "line"
    if evidence_id.startswith("notes:"):
        return "notes"
    return "unknown"


def _evidence_source_counts(evidence_ids: tuple[str, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for evidence_id in evidence_ids:
        source = _source_from_evidence_id(evidence_id)
        counts[source] = counts.get(source, 0) + 1
    return counts


def _temporal_insufficient_reason(
    diagnostics: dict[str, Any],
    *,
    should_use_count: int,
) -> str | None:
    if should_use_count > 0:
        return None
    if int(diagnostics.get("photo_candidates_examined") or 0) == 0:
        return "no photos were found in the parsed date range"
    return "candidate evidence was found, but outing likelihood was weak"


def _model_status(config: ConfigBundle) -> list[dict[str, Any]]:
    endpoints = configured_model_endpoints(config.model_registry, include_disabled=False)
    return [
        {
            "model_id": endpoint.model_id,
            "provider": endpoint.provider,
            "role": endpoint.role,
            "endpoint_configured": True,
            "endpoint_url": endpoint.base_url,
            "ping_status": "not_checked",
        }
        for endpoint in endpoints
    ]


def _unique_strings(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return tuple(result)
