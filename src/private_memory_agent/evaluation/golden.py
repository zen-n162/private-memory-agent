"""Golden question evaluation over existing local evidence."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from private_memory_agent.agent import (
    LeaderRetrievalPlanner,
    RetrievalPlan,
    RetrievalPlanMetadata,
    RetrievalPlanner,
)
from private_memory_agent.config import load_config
from private_memory_agent.config.loader import ConfigError, PROJECT_ROOT, _parse_simple_yaml
from private_memory_agent.e2e import (
    DEFAULT_E2E_DB_PATH,
    DEFAULT_E2E_LEADER_MODEL_KEY,
    DEFAULT_E2E_MAX_EVIDENCE_CHARS,
    DEFAULT_E2E_MAX_EVIDENCE_ITEMS,
    DEFAULT_E2E_QUERY_LIMIT,
    DEFAULT_E2E_REAL_MODEL_MAX_TOKENS,
    E2ESmokeOptions,
    E2ESmokeQuery,
    run_e2e_smoke,
)
from private_memory_agent.retrieval import RERANKER_MODEL_CHOICES, SEMANTIC_MODEL_CHOICES
from private_memory_agent.retrieval.text import normalize_text
from private_memory_agent.runtime import (
    OpenAICompatibleHTTPClient,
    endpoint_from_model_spec,
    preflight_chat_endpoint,
)

DEFAULT_GOLDEN_QUESTIONS_FILENAME = "golden_questions.example.yaml"
LOCAL_GOLDEN_QUESTIONS_FILENAME = "golden_questions.local.yaml"
_SUPPORTED_GOLDEN_SOURCES = {"photos", "line", "notes"}
_MANUAL_RATING_FIELDS = (
    "answer_correctness",
    "evidence_relevance",
    "source_coverage",
    "uncertainty_handling",
    "privacy_safety",
    "source_policy_passed",
    "evidence_relevance_score",
    "expected_keywords_hit_count",
    "missing_expected_keywords",
    "usable_evidence_notes",
    "repair_notes",
    "source_mismatch_notes",
    "irrelevant_evidence_notes",
    "notes",
)


@dataclass(frozen=True)
class GoldenQuestion:
    """One local golden question.

    Question text may be private and is not included in default reports.
    """

    question_id: str
    text: str
    sources: tuple[str, ...] = ()
    category: str = "general"
    expected_sources: tuple[str, ...] = ()
    required_sources: tuple[str, ...] = ()
    preferred_sources: tuple[str, ...] = ()
    excluded_sources: tuple[str, ...] = ()
    expected_keywords: tuple[str, ...] = ()
    optional_keywords: tuple[str, ...] = ()
    negative_keywords: tuple[str, ...] = ()
    evaluation_focus: tuple[str, ...] = ()
    source_policy: str | None = None


@dataclass(frozen=True)
class GoldenEvalOptions:
    """Options for golden question answer-quality evaluation."""

    config_dir: Path | str | None = None
    paths_config: Path | str | None = None
    db_path: Path | str = DEFAULT_E2E_DB_PATH
    questions_config: Path | str | None = None
    retrieval_only: bool = False
    fake_model: bool = False
    real_model: bool = False
    query_limit: int | None = None
    query_id: str | None = None
    limit: int = DEFAULT_E2E_QUERY_LIMIT
    timeout_seconds: float | None = None
    max_tokens: int = DEFAULT_E2E_REAL_MODEL_MAX_TOKENS
    max_evidence_items: int = DEFAULT_E2E_MAX_EVIDENCE_ITEMS
    max_evidence_chars: int = DEFAULT_E2E_MAX_EVIDENCE_CHARS
    compact_evidence: bool = True
    json_retry: int = 1
    response_format_json: bool = False
    show_answer: bool = False
    show_snippets: bool = False
    snippet_chars: int = 160
    require_sources: tuple[str, ...] = ()
    exclude_sources: tuple[str, ...] = ()
    preferred_sources: tuple[str, ...] = ()
    source_policy: str = "soft"
    expected_keywords: tuple[str, ...] = ()
    negative_keywords: tuple[str, ...] = ()
    keyword_policy: str = "soft"
    leader_plan: bool = False
    leader_rerank: bool = False
    retrieval_repair: int = 0
    show_plan: bool = False
    show_relevance: bool = False
    minimum_relevance_score: float = 0.6
    require_usable_evidence: bool = False
    relevance_policy: str = "soft"
    semantic_model: str = "none"
    semantic_top_k: int | None = None
    semantic_weight: float = 1.0
    reranker: str = "none"
    rerank_top_k: int | None = None
    retrieval_planner: RetrievalPlanner | None = None
    model_key: str = DEFAULT_E2E_LEADER_MODEL_KEY
    allow_remote: bool = False


@dataclass(frozen=True)
class GoldenQuestionResult:
    """Privacy-safe per-question evaluation result."""

    question_id: str
    category: str
    retrieval_succeeded: bool
    answer_succeeded: bool
    evidence_count: int
    candidate_retrieval_succeeded: bool = False
    usable_evidence_succeeded: bool = False
    usable_evidence_count: int = 0
    unusable_evidence_count: int = 0
    should_use_evidence_count: int = 0
    evidence_ids: tuple[str, ...] = ()
    evidence_source_counts: dict[str, int] = field(default_factory=dict)
    used_sources: tuple[str, ...] = ()
    evidence_reference_count: int = 0
    unknown_evidence_reference_count: int = 0
    confidence: float | None = None
    unknowns_count: int = 0
    json_retry_used: bool = False
    json_retry_succeeded: bool = False
    answer_validation_error: str | None = None
    privacy_safe_output: bool = True
    answer_conclusion: str | None = None
    answer_unknowns: tuple[str, ...] = ()
    safe_snippets: tuple[dict[str, str], ...] = ()
    requested_sources: tuple[str, ...] = ()
    expected_sources: tuple[str, ...] = ()
    required_sources: tuple[str, ...] = ()
    preferred_sources: tuple[str, ...] = ()
    excluded_sources: tuple[str, ...] = ()
    missing_expected_sources: tuple[str, ...] = ()
    missing_required_sources: tuple[str, ...] = ()
    excluded_source_violations: tuple[str, ...] = ()
    source_policy: str = "soft"
    retrieval_passed_source_policy: bool = True
    expected_keywords_count: int = 0
    optional_keywords_count: int = 0
    expected_keywords_hit_count: int = 0
    expected_keyword_hit_evidence_count: int = 0
    missing_expected_keywords: tuple[str, ...] = ()
    negative_keywords_count: int = 0
    negative_keyword_hit_count: int = 0
    evidence_keyword_hit_counts: dict[str, int] = field(default_factory=dict)
    relevance_score: float = 0.0
    source_coverage_score: float = 0.0
    keyword_relevance_score: float = 0.0
    plan_relevance_score: float | None = None
    final_relevance_score: float = 0.0
    minimum_relevance_threshold: float = 0.6
    relevance_policy: str = "soft"
    relevance_policy_passed: bool = True
    insufficient_evidence_reason: str | None = None
    keyword_policy: str = "soft"
    retrieval_passed_keyword_policy: bool = True
    plan_metadata: RetrievalPlanMetadata = field(default_factory=RetrievalPlanMetadata)
    retrieval_repair_count: int = 0
    repair_attempted: bool = False
    repair_improved: bool = False
    repair_reason: str | None = None
    pre_repair_usable_evidence_count: int | None = None
    post_repair_usable_evidence_count: int | None = None
    repair_query_count: int = 0
    repair_queries_created_count: int = 0
    repair_specific_query_count: int = 0
    repair_generic_query_count: int = 0
    repair_used_specific_concepts: bool = False
    repair_used_main_entities: bool = False
    leader_rerank_used: bool = False
    relevance_judged: bool = False
    average_plan_relevance_score: float | None = None
    plan_relevance_should_use_count: int = 0
    plan_relevance_specificity_counts: dict[str, int] = field(default_factory=dict)
    relevance_scores: tuple[dict[str, Any], ...] = ()
    semantic_enabled: bool = False
    semantic_model: str = "none"
    semantic_embedding_model_id: str | None = None
    semantic_candidate_count: int = 0
    semantic_top_k: int | None = None
    semantic_weight: float = 1.0
    reranker: str = "none"
    reranker_model_id: str | None = None
    reranked_candidate_count: int = 0
    evaluation_focus: tuple[str, ...] = ()
    manual_ratings: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return (
            self.retrieval_succeeded
            and self.retrieval_passed_source_policy
            and self.retrieval_passed_keyword_policy
            and self.relevance_policy_passed
            and (self.answer_succeeded or self.confidence is None)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "category": self.category,
            "retrieval_succeeded": self.retrieval_succeeded,
            "answer_succeeded": self.answer_succeeded,
            "evidence_count": self.evidence_count,
            "candidate_retrieval_succeeded": self.candidate_retrieval_succeeded,
            "usable_evidence_succeeded": self.usable_evidence_succeeded,
            "usable_evidence_count": self.usable_evidence_count,
            "unusable_evidence_count": self.unusable_evidence_count,
            "should_use_evidence_count": self.should_use_evidence_count,
            "evidence_ids": list(self.evidence_ids),
            "evidence_source_counts": dict(self.evidence_source_counts),
            "used_sources": list(self.used_sources),
            "evidence_reference_count": self.evidence_reference_count,
            "unknown_evidence_reference_count": self.unknown_evidence_reference_count,
            "confidence": self.confidence,
            "unknowns_count": self.unknowns_count,
            "json_retry_used": self.json_retry_used,
            "json_retry_succeeded": self.json_retry_succeeded,
            "answer_validation_error": self.answer_validation_error,
            "privacy_safe_output": self.privacy_safe_output,
            "answer_conclusion": self.answer_conclusion,
            "answer_unknowns": list(self.answer_unknowns),
            "safe_snippets": [dict(item) for item in self.safe_snippets],
            "requested_sources": list(self.requested_sources),
            "expected_sources": list(self.expected_sources),
            "required_sources": list(self.required_sources),
            "preferred_sources": list(self.preferred_sources),
            "excluded_sources": list(self.excluded_sources),
            "missing_expected_sources": list(self.missing_expected_sources),
            "missing_required_sources": list(self.missing_required_sources),
            "excluded_source_violations": list(self.excluded_source_violations),
            "source_policy": self.source_policy,
            "retrieval_passed_source_policy": self.retrieval_passed_source_policy,
            "expected_keywords_count": self.expected_keywords_count,
            "optional_keywords_count": self.optional_keywords_count,
            "expected_keywords_hit_count": self.expected_keywords_hit_count,
            "expected_keyword_hit_evidence_count": self.expected_keyword_hit_evidence_count,
            "missing_expected_keywords": list(self.missing_expected_keywords),
            "negative_keywords_count": self.negative_keywords_count,
            "negative_keyword_hit_count": self.negative_keyword_hit_count,
            "evidence_keyword_hit_counts": dict(self.evidence_keyword_hit_counts),
            "relevance_score": self.relevance_score,
            "source_coverage_score": self.source_coverage_score,
            "keyword_relevance_score": self.keyword_relevance_score,
            "plan_relevance_score": self.plan_relevance_score,
            "final_relevance_score": self.final_relevance_score,
            "minimum_relevance_threshold": self.minimum_relevance_threshold,
            "relevance_policy": self.relevance_policy,
            "relevance_policy_passed": self.relevance_policy_passed,
            "insufficient_evidence_reason": self.insufficient_evidence_reason,
            "keyword_policy": self.keyword_policy,
            "retrieval_passed_keyword_policy": self.retrieval_passed_keyword_policy,
            "plan": self.plan_metadata.to_dict(),
            "retrieval_repair_count": self.retrieval_repair_count,
            "repair_attempted": self.repair_attempted,
            "repair_improved": self.repair_improved,
            "repair_reason": self.repair_reason,
            "pre_repair_usable_evidence_count": self.pre_repair_usable_evidence_count,
            "post_repair_usable_evidence_count": self.post_repair_usable_evidence_count,
            "repair_query_count": self.repair_query_count,
            "repair_queries_created_count": self.repair_queries_created_count,
            "repair_specific_query_count": self.repair_specific_query_count,
            "repair_generic_query_count": self.repair_generic_query_count,
            "repair_used_specific_concepts": self.repair_used_specific_concepts,
            "repair_used_main_entities": self.repair_used_main_entities,
            "leader_rerank_used": self.leader_rerank_used,
            "relevance_judged": self.relevance_judged,
            "average_plan_relevance_score": self.average_plan_relevance_score,
            "plan_relevance_should_use_count": self.plan_relevance_should_use_count,
            "plan_relevance_specificity_counts": dict(self.plan_relevance_specificity_counts),
            "relevance_scores": [dict(item) for item in self.relevance_scores],
            "semantic_enabled": self.semantic_enabled,
            "semantic_model": self.semantic_model,
            "semantic_embedding_model_id": self.semantic_embedding_model_id,
            "semantic_candidate_count": self.semantic_candidate_count,
            "semantic_top_k": self.semantic_top_k,
            "semantic_weight": self.semantic_weight,
            "reranker": self.reranker,
            "reranker_model_id": self.reranker_model_id,
            "reranked_candidate_count": self.reranked_candidate_count,
            "evaluation_focus": list(self.evaluation_focus),
            "manual_ratings": dict(self.manual_ratings),
            "passed": self.passed,
        }


@dataclass(frozen=True)
class GoldenEvalSummary:
    """Aggregate golden evaluation counters."""

    question_count: int = 0
    retrieval_succeeded_count: int = 0
    answer_succeeded_count: int = 0
    answer_validation_error_count: int = 0
    retry_used_count: int = 0
    retry_success_count: int = 0
    average_confidence: float | None = None
    privacy_safe_output_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_count": self.question_count,
            "retrieval_succeeded_count": self.retrieval_succeeded_count,
            "answer_succeeded_count": self.answer_succeeded_count,
            "answer_validation_error_count": self.answer_validation_error_count,
            "retry_used_count": self.retry_used_count,
            "retry_success_count": self.retry_success_count,
            "average_confidence": self.average_confidence,
            "privacy_safe_output_count": self.privacy_safe_output_count,
        }


@dataclass(frozen=True)
class GoldenEvalReport:
    """Golden question evaluation report."""

    mode: str
    ok: bool
    questions_config_path: str
    db_exists: bool
    summary: GoldenEvalSummary
    results: tuple[GoldenQuestionResult, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "mode": self.mode,
            "questions_config_path": self.questions_config_path,
            "db_exists": self.db_exists,
            "summary": self.summary.to_dict(),
            "results": [result.to_dict() for result in self.results],
            "warnings": list(self.warnings),
            "privacy": {
                "default_output": (
                    "question ids, counts, source labels, evidence ids, and "
                    "status only"
                ),
                "hidden_by_default": (
                    "question text, answer text, snippets, filenames, paths, "
                    "GPS, EXIF, OCR, LINE text, note bodies, captions, raw model output"
                ),
            },
        }


@dataclass(frozen=True)
class _GoldenSourceConstraints:
    """Effective source constraints for one golden question."""

    requested_sources: tuple[str, ...]
    expected_sources: tuple[str, ...]
    required_sources: tuple[str, ...]
    preferred_sources: tuple[str, ...]
    excluded_sources: tuple[str, ...]
    source_policy: str
    expected_keywords: tuple[str, ...]
    optional_keywords: tuple[str, ...]
    negative_keywords: tuple[str, ...]
    keyword_policy: str
    retrieval_text: str
    plan: RetrievalPlan | None = None
    plan_metadata: RetrievalPlanMetadata = field(default_factory=RetrievalPlanMetadata)


@dataclass(frozen=True)
class _RepairDiagnostics:
    """Privacy-safe repair-loop counters for one golden question."""

    attempted: bool = False
    count: int = 0
    improved: bool = False
    reason: str | None = None
    pre_usable_count: int | None = None
    post_usable_count: int | None = None
    repair_query_count: int = 0
    repair_queries_created_count: int = 0
    repair_specific_query_count: int = 0
    repair_generic_query_count: int = 0
    repair_used_specific_concepts: bool = False
    repair_used_main_entities: bool = False


def load_golden_questions(
    config_dir: Path | str | None = None,
    *,
    questions_config: Path | str | None = None,
) -> tuple[GoldenQuestion, ...]:
    """Load golden questions from local override, example config, or defaults."""

    path = _resolve_golden_questions_path(config_dir, questions_config=questions_config)
    if not path.exists():
        return _default_golden_questions()
    try:
        raw = _load_golden_yaml(path)
    except (OSError, ConfigError) as exc:
        raise ValueError("unable to load golden question config") from exc
    questions = raw.get("questions")
    if questions is None:
        raise ValueError("golden question config must contain a questions section")
    loaded = _parse_questions(questions)
    if not loaded:
        raise ValueError("golden question config did not contain usable questions")
    return tuple(loaded)


def run_golden_eval(options: GoldenEvalOptions) -> GoldenEvalReport:
    """Run golden question evaluation using the E2E retrieval/answer path."""

    if options.snippet_chars <= 0:
        raise ValueError("snippet_chars must be positive")
    if options.retrieval_repair < 0:
        raise ValueError("retrieval_repair must be non-negative")
    if not 0.0 <= options.minimum_relevance_score <= 1.0:
        raise ValueError("minimum_relevance_score must be between 0.0 and 1.0")
    if options.relevance_policy not in {"soft", "strict"}:
        raise ValueError("relevance_policy must be soft or strict")
    if options.semantic_model not in SEMANTIC_MODEL_CHOICES:
        allowed = ", ".join(SEMANTIC_MODEL_CHOICES)
        raise ValueError(f"semantic_model must be one of: {allowed}")
    if options.semantic_top_k is not None and options.semantic_top_k <= 0:
        raise ValueError("semantic_top_k must be positive")
    if options.semantic_weight < 0:
        raise ValueError("semantic_weight must be non-negative")
    if options.reranker not in RERANKER_MODEL_CHOICES:
        allowed = ", ".join(RERANKER_MODEL_CHOICES)
        raise ValueError(f"reranker must be one of: {allowed}")
    if options.rerank_top_k is not None and options.rerank_top_k <= 0:
        raise ValueError("rerank_top_k must be positive")
    questions_path = _resolve_golden_questions_path(
        options.config_dir,
        questions_config=options.questions_config,
    )
    questions = _select_golden_questions(
        load_golden_questions(options.config_dir, questions_config=options.questions_config),
        query_id=options.query_id,
        query_limit=options.query_limit,
    )
    mode = _resolve_golden_mode(options)
    planned = _plan_questions(questions, options)
    constrained_questions = tuple(
        _constrained_question(
            question,
            options,
            plan=planned.get(question.question_id),
        )
        for question in questions
    )
    e2e_report = _run_e2e_for_golden(
        questions=questions,
        constrained_questions=constrained_questions,
        options=options,
        mode=mode,
    )
    e2e_results, repair_diagnostics = _repair_e2e_results_if_needed(
        questions=questions,
        constrained_questions=constrained_questions,
        e2e_results=tuple(e2e_report.query_results),
        options=options,
        mode=mode,
    )
    results = tuple(
        _golden_result_from_e2e(
            question,
            constrained,
            result,
            options=options,
            repair_diagnostics=repair_diagnostics.get(question.question_id, _RepairDiagnostics()),
        )
        for question, constrained, result in zip(
            questions,
            constrained_questions,
            e2e_results,
            strict=False,
        )
    )
    ok = e2e_report.db_exists and bool(results) and all(result.passed for result in results)
    if mode != "retrieval_only":
        ok = ok and all(result.answer_succeeded for result in results)
    relevance_warnings = tuple(
        f"{result.question_id}: {result.insufficient_evidence_reason}"
        for result in results
        if result.insufficient_evidence_reason
    )
    return GoldenEvalReport(
        mode=mode,
        ok=ok,
        questions_config_path=_safe_config_path(questions_path),
        db_exists=e2e_report.db_exists,
        summary=_summary_from_results(results),
        results=results,
        warnings=tuple(
            (
                *e2e_report.warnings,
                *relevance_warnings,
                *(
                    ("retrieval plan display requested; plan text may contain private question-derived content",)
                    if options.show_plan
                    else ()
                ),
                *(
                    ("relevance display requested; relevance metadata may reveal private local concepts",)
                    if options.show_relevance
                    else ()
                ),
            ),
        ),
    )


def format_golden_eval_report(report: GoldenEvalReport) -> str:
    """Render a privacy-safe Markdown golden evaluation report."""

    summary = report.summary
    lines = [
        "# Golden Question Evaluation",
        "",
        f"- status: {'passed' if report.ok else 'needs-attention'}",
        f"- mode: {report.mode}",
        f"- db_exists: {str(report.db_exists).lower()}",
        f"- questions_config: {report.questions_config_path}",
        f"- question_count: {summary.question_count}",
        f"- retrieval_succeeded: {summary.retrieval_succeeded_count}",
        f"- answer_succeeded: {summary.answer_succeeded_count}",
        f"- answer_validation_errors: {summary.answer_validation_error_count}",
        f"- retry_used: {summary.retry_used_count}",
        f"- retry_success: {summary.retry_success_count}",
        f"- average_confidence: {summary.average_confidence}",
        "",
        "## Privacy",
        "",
        "Default report output hides question text, answer text, evidence snippets, "
        "filenames, full paths, GPS, EXIF, OCR, LINE text, note bodies, captions, "
        "and raw model output.",
    ]
    if any(result.answer_conclusion is not None for result in report.results):
        lines.append(
            "WARNING: --show-answer was used. Answer text may contain private "
            "evidence-derived content. Do not paste this report into public chats.",
        )
    if any(result.safe_snippets for result in report.results):
        lines.append(
            "WARNING: --show-snippets was used. Snippets may contain private local "
            "content even when truncated.",
        )
    if report.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in report.warnings)
    lines.extend(["", "## Per-question Results", ""])
    for result in report.results:
        lines.extend(_format_question_markdown(result))
    return "\n".join(lines).rstrip() + "\n"


def report_to_json(report: GoldenEvalReport) -> str:
    """Serialize a golden report as deterministic JSON."""

    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def report_to_jsonl(report: GoldenEvalReport) -> str:
    """Serialize a golden report as JSONL records."""

    lines = [
        json.dumps(
            {"record_type": "summary", **report.summary.to_dict(), "ok": report.ok},
            ensure_ascii=False,
            sort_keys=True,
        ),
    ]
    lines.extend(
        json.dumps(
            {"record_type": "question", **result.to_dict()},
            ensure_ascii=False,
            sort_keys=True,
        )
        for result in report.results
    )
    return "\n".join(lines) + "\n"


def write_golden_outputs(
    report: GoldenEvalReport,
    *,
    markdown_path: Path | str | None = None,
    jsonl_path: Path | str | None = None,
) -> tuple[Path | None, Path | None]:
    """Write optional Markdown and JSONL outputs."""

    written_markdown = _write_text(markdown_path, format_golden_eval_report(report))
    written_jsonl = _write_text(jsonl_path, report_to_jsonl(report))
    return written_markdown, written_jsonl


def _plan_questions(
    questions: tuple[GoldenQuestion, ...],
    options: GoldenEvalOptions,
) -> dict[str, RetrievalPlan]:
    if not options.leader_plan and options.retrieval_planner is None:
        return {}
    planner = options.retrieval_planner or _build_leader_retrieval_planner(options)
    planned: dict[str, RetrievalPlan] = {}
    for question in questions:
        planned[question.question_id] = planner.plan(question.text)
    return planned


def _build_leader_retrieval_planner(options: GoldenEvalOptions) -> RetrievalPlanner:
    config = load_config(config_dir=options.config_dir, paths_config=options.paths_config)
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
        else endpoint.request_timeout_seconds or 300.0
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
        response_format_json=options.response_format_json,
    )


def _run_e2e_for_golden(
    *,
    questions: tuple[GoldenQuestion, ...],
    constrained_questions: tuple[_GoldenSourceConstraints, ...],
    options: GoldenEvalOptions,
    mode: str,
) -> Any:
    return run_e2e_smoke(
        E2ESmokeOptions(
            config_dir=options.config_dir,
            paths_config=options.paths_config,
            db_path=options.db_path,
            queries=tuple(
                _e2e_query_from_golden(question, constrained, options=options)
                for question, constrained in zip(questions, constrained_questions, strict=True)
            ),
            retrieval_only=mode == "retrieval_only",
            fake_model=mode == "fake_model",
            real_model=mode == "real_model",
            limit=options.limit,
            timeout_seconds=options.timeout_seconds,
            max_tokens=options.max_tokens,
            max_evidence_items=options.max_evidence_items,
            max_evidence_chars=options.max_evidence_chars,
            compact_evidence=options.compact_evidence,
            json_retry=options.json_retry,
            response_format_json=options.response_format_json,
            show_answer=options.show_answer,
            show_snippets=options.show_snippets,
            snippet_chars=options.snippet_chars,
            semantic_model=options.semantic_model,
            semantic_top_k=options.semantic_top_k,
            semantic_weight=options.semantic_weight,
            reranker=options.reranker,
            rerank_top_k=options.rerank_top_k,
            model_key=options.model_key,
            allow_remote=options.allow_remote,
            no_fallback=True,
        ),
    )


def _e2e_query_from_golden(
    question: GoldenQuestion,
    constrained: _GoldenSourceConstraints,
    *,
    options: GoldenEvalOptions,
) -> E2ESmokeQuery:
    return E2ESmokeQuery(
        query_id=question.question_id,
        text=question.text,
        sources=constrained.requested_sources,
        retrieval_text=constrained.retrieval_text,
        expected_terms=constrained.expected_keywords,
        boost_terms=constrained.expected_keywords + constrained.optional_keywords,
        negative_terms=constrained.negative_keywords,
        preferred_sources=constrained.preferred_sources,
        retrieval_plan=constrained.plan,
        judge_relevance=bool(constrained.plan and (options.leader_rerank or options.show_relevance)),
        rerank_by_relevance=bool(constrained.plan and options.leader_rerank),
        show_relevance=options.show_relevance,
        semantic_enabled=options.semantic_model != "none",
        semantic_top_k=options.semantic_top_k,
        semantic_weight=options.semantic_weight,
        reranker=options.reranker,
        rerank_top_k=options.rerank_top_k,
    )


def _repair_e2e_results_if_needed(
    *,
    questions: tuple[GoldenQuestion, ...],
    constrained_questions: tuple[_GoldenSourceConstraints, ...],
    e2e_results: tuple[Any, ...],
    options: GoldenEvalOptions,
    mode: str,
) -> tuple[tuple[Any, ...], dict[str, _RepairDiagnostics]]:
    if options.retrieval_repair <= 0:
        return e2e_results, {}
    repaired_results = list(e2e_results)
    repair_diagnostics: dict[str, _RepairDiagnostics] = {}
    for index, (question, constrained, result) in enumerate(
        zip(questions, constrained_questions, e2e_results, strict=False),
    ):
        if not _needs_retrieval_repair(result, constrained, options):
            continue
        repair_profile = _repair_query_profile(constrained.plan)
        pre_usable_count = _usable_evidence_count_from_e2e(
            result,
            minimum_relevance_score=options.minimum_relevance_score,
        )
        repair_reason = _repair_reason(result, constrained)
        repaired = replace(
            constrained,
            retrieval_text=_expanded_retrieval_text(
                question.text,
                expected_keywords=constrained.expected_keywords,
                optional_keywords=constrained.optional_keywords,
                plan=constrained.plan,
                repair=True,
            ),
        )
        repair_report = _run_e2e_for_golden(
            questions=(question,),
            constrained_questions=(repaired,),
            options=options,
            mode=mode,
        )
        if repair_report.query_results:
            repaired_result = repair_report.query_results[0]
            post_usable_count = _usable_evidence_count_from_e2e(
                repaired_result,
                minimum_relevance_score=options.minimum_relevance_score,
            )
            repaired_results[index] = repaired_result
            repair_diagnostics[question.question_id] = _RepairDiagnostics(
                attempted=True,
                count=1,
                improved=post_usable_count > pre_usable_count,
                reason=repair_reason,
                pre_usable_count=pre_usable_count,
                post_usable_count=post_usable_count,
                repair_query_count=len(constrained.plan.retrieval_queries)
                if constrained.plan is not None
                else 0,
                repair_queries_created_count=repair_profile["created_count"],
                repair_specific_query_count=repair_profile["specific_count"],
                repair_generic_query_count=repair_profile["generic_count"],
                repair_used_specific_concepts=repair_profile["used_specific_concepts"],
                repair_used_main_entities=repair_profile["used_main_entities"],
            )
        else:
            repair_diagnostics[question.question_id] = _RepairDiagnostics(
                attempted=True,
                reason=repair_reason,
                pre_usable_count=pre_usable_count,
                post_usable_count=pre_usable_count,
                repair_query_count=len(constrained.plan.retrieval_queries)
                if constrained.plan is not None
                else 0,
                repair_queries_created_count=repair_profile["created_count"],
                repair_specific_query_count=repair_profile["specific_count"],
                repair_generic_query_count=repair_profile["generic_count"],
                repair_used_specific_concepts=repair_profile["used_specific_concepts"],
                repair_used_main_entities=repair_profile["used_main_entities"],
            )
    return tuple(repaired_results), repair_diagnostics


def _needs_retrieval_repair(
    result: Any,
    constrained: _GoldenSourceConstraints,
    options: GoldenEvalOptions,
) -> bool:
    if constrained.plan is None:
        return False
    if result.evidence_count == 0:
        return True
    usable_count = _usable_evidence_count_from_e2e(
        result,
        minimum_relevance_score=options.minimum_relevance_score,
    )
    if result.plan_relevance_judged and usable_count == 0:
        return True
    if constrained.expected_keywords and result.expected_keywords_hit_count == 0:
        return True
    return False


def _repair_reason(result: Any, constrained: _GoldenSourceConstraints) -> str:
    if result.evidence_count == 0:
        return "no candidate evidence was retrieved"
    if result.plan_relevance_judged and (result.plan_relevance_should_use_count or 0) == 0:
        return "candidate evidence was found, but relevance judge found no usable evidence"
    if constrained.expected_keywords and result.expected_keywords_hit_count == 0:
        return "candidate evidence did not hit expected keywords"
    return "planned retrieval was weak"


def _repair_query_profile(plan: RetrievalPlan | None) -> dict[str, Any]:
    if plan is None:
        return {
            "created_count": 0,
            "specific_count": 0,
            "generic_count": 0,
            "used_specific_concepts": False,
            "used_main_entities": False,
        }
    specific_terms = _unique_strings(plan.specific_concepts + plan.main_entities)
    generic_terms = _unique_strings(plan.generic_concepts)
    created = _unique_strings(specific_terms or plan.retrieval_queries)
    generic_count = sum(1 for query in created if normalize_text(query) in _normalized_set(generic_terms))
    return {
        "created_count": len(created),
        "specific_count": max(0, len(created) - generic_count),
        "generic_count": generic_count,
        "used_specific_concepts": bool(plan.specific_concepts),
        "used_main_entities": bool(plan.main_entities),
    }


def _normalized_set(values: tuple[str, ...]) -> set[str]:
    return {normalize_text(value) for value in values if normalize_text(value)}


def _constrained_question(
    question: GoldenQuestion,
    options: GoldenEvalOptions,
    *,
    plan: RetrievalPlan | None = None,
) -> _GoldenSourceConstraints:
    source_policy = (
        "strict"
        if options.source_policy == "strict"
        else (question.source_policy or options.source_policy or "soft")
    ).strip().lower()
    if source_policy not in {"soft", "strict"}:
        raise ValueError("source_policy must be soft or strict")
    keyword_policy = (options.keyword_policy or "soft").strip().lower()
    if keyword_policy not in {"soft", "strict"}:
        raise ValueError("keyword_policy must be soft or strict")

    expected_sources = _unique_sources(question.expected_sources)
    required_sources = _unique_sources(question.required_sources + options.require_sources)
    plan_preferences = plan.source_preferences if plan is not None else ()
    plan_constraints = plan.source_constraints if plan is not None else ()
    preferred_sources = _unique_sources(
        question.preferred_sources + options.preferred_sources + plan_preferences,
    )
    excluded_sources = _unique_sources(question.excluded_sources + options.exclude_sources)
    expected_keywords = _unique_strings(question.expected_keywords + options.expected_keywords)
    optional_keywords = _unique_strings(
        question.optional_keywords + (plan.specific_concepts if plan is not None else ()),
    )
    negative_keywords = _unique_strings(
        question.negative_keywords
        + options.negative_keywords
        + (plan.excluded_concepts if plan is not None else ()),
    )

    hard_sources = question.sources or expected_sources or required_sources or plan_constraints
    if hard_sources:
        requested = _unique_sources(hard_sources + preferred_sources)
    else:
        requested = tuple(
            source
            for source in ("photos", "line", "notes")
            if source not in excluded_sources
        )

    requested = tuple(source for source in requested if source not in excluded_sources)
    if not requested:
        requested = tuple(source for source in required_sources if source not in excluded_sources)
    return _GoldenSourceConstraints(
        requested_sources=requested,
        expected_sources=expected_sources,
        required_sources=required_sources,
        preferred_sources=preferred_sources,
        excluded_sources=excluded_sources,
        source_policy=source_policy,
        expected_keywords=expected_keywords,
        optional_keywords=optional_keywords,
        negative_keywords=negative_keywords,
        keyword_policy=keyword_policy,
        retrieval_text=_expanded_retrieval_text(
            question.text,
            expected_keywords=expected_keywords,
            optional_keywords=optional_keywords,
            plan=plan,
            repair=False,
        ),
        plan=plan,
        plan_metadata=plan.metadata(show_plan=options.show_plan)
        if plan is not None
        else RetrievalPlanMetadata(),
    )


def _golden_result_from_e2e(
    question: GoldenQuestion,
    constraints: _GoldenSourceConstraints,
    result: Any,
    *,
    options: GoldenEvalOptions,
    repair_diagnostics: _RepairDiagnostics,
) -> GoldenQuestionResult:
    unknown_reference_count = 0
    if result.error_message and "unknown_evidence_reference" in result.error_message:
        unknown_reference_count = 1
    actual_sources = {
        source for source, count in result.evidence_source_counts.items() if count > 0
    }
    missing_expected = tuple(
        source for source in constraints.expected_sources if source not in actual_sources
    )
    missing_required = tuple(
        source for source in constraints.required_sources if source not in actual_sources
    )
    excluded_violations = tuple(
        source for source in constraints.excluded_sources if source in actual_sources
    )
    if constraints.source_policy == "strict":
        source_policy_passed = (
            not missing_required
            and not missing_expected
            and not excluded_violations
        )
    else:
        source_policy_passed = not excluded_violations
    retrieval_passed_keyword_policy = True
    if constraints.keyword_policy == "strict":
        retrieval_passed_keyword_policy = (
            not result.missing_expected_keywords
            and int(result.negative_keyword_hit_count or 0) == 0
        )
    keyword_snippets = _snippets_with_keyword_labels(
        result.safe_snippets,
        expected_keywords=constraints.expected_keywords,
        optional_keywords=constraints.optional_keywords,
    )
    candidate_retrieval_succeeded = bool(result.retrieval_succeeded)
    should_use_count = _usable_evidence_count_from_e2e(
        result,
        minimum_relevance_score=options.minimum_relevance_score,
    )
    usable_evidence_succeeded = candidate_retrieval_succeeded and should_use_count > 0
    source_coverage_score = _source_coverage_score(
        retrieval_succeeded=candidate_retrieval_succeeded,
        source_policy_passed=source_policy_passed,
        expected_sources=constraints.expected_sources,
        actual_sources=actual_sources,
    )
    keyword_relevance_score = _keyword_relevance_score(
        retrieval_succeeded=candidate_retrieval_succeeded,
        expected_keyword_count=len(constraints.expected_keywords),
        expected_keyword_hit_count=int(result.expected_keywords_hit_count or 0),
        negative_keyword_hit_count=int(result.negative_keyword_hit_count or 0),
    )
    plan_relevance_score = _plan_relevance_score_from_e2e(
        result,
        minimum_relevance_score=options.minimum_relevance_score,
    )
    final_relevance_score = _final_relevance_score(
        retrieval_succeeded=candidate_retrieval_succeeded,
        evidence_count=result.evidence_count,
        source_coverage_score=source_coverage_score,
        keyword_relevance_score=keyword_relevance_score,
        plan_relevance_score=plan_relevance_score,
        should_use_evidence_count=should_use_count,
        plan_relevance_judged=bool(result.plan_relevance_judged),
        limit=options.limit,
    )
    relevance_policy_passed = _relevance_policy_passed(
        options=options,
        usable_evidence_succeeded=usable_evidence_succeeded,
        final_relevance_score=final_relevance_score,
        plan_relevance_judged=bool(result.plan_relevance_judged),
    )
    insufficient_reason = _insufficient_evidence_reason(
        result,
        usable_evidence_succeeded=usable_evidence_succeeded,
        final_relevance_score=final_relevance_score,
        minimum_relevance_score=options.minimum_relevance_score,
    )
    return GoldenQuestionResult(
        question_id=question.question_id,
        category=question.category,
        retrieval_succeeded=result.retrieval_succeeded,
        answer_succeeded=result.answer_succeeded,
        evidence_count=result.evidence_count,
        candidate_retrieval_succeeded=candidate_retrieval_succeeded,
        usable_evidence_succeeded=usable_evidence_succeeded,
        usable_evidence_count=should_use_count,
        unusable_evidence_count=max(0, int(result.evidence_count or 0) - should_use_count),
        should_use_evidence_count=should_use_count,
        evidence_ids=result.evidence_ids,
        evidence_source_counts=result.evidence_source_counts,
        used_sources=result.used_sources,
        evidence_reference_count=len(result.answer_evidence_references),
        unknown_evidence_reference_count=unknown_reference_count,
        confidence=result.answer_confidence,
        unknowns_count=int(result.answer_unknown_count or 0),
        json_retry_used=bool(result.json_retry_used),
        json_retry_succeeded=bool(result.json_retry_succeeded),
        answer_validation_error=result.answer_validation_error_message
        if result.error_class == "AnswerValidationError"
        else None,
        privacy_safe_output=not bool(result.raw_model_output_preview),
        answer_conclusion=result.answer_conclusion if options.show_answer else None,
        answer_unknowns=result.answer_unknowns if options.show_answer else (),
        requested_sources=constraints.requested_sources,
        expected_sources=constraints.expected_sources,
        required_sources=constraints.required_sources,
        preferred_sources=constraints.preferred_sources,
        excluded_sources=constraints.excluded_sources,
        missing_expected_sources=missing_expected,
        missing_required_sources=missing_required,
        excluded_source_violations=excluded_violations,
        source_policy=constraints.source_policy,
        retrieval_passed_source_policy=source_policy_passed,
        expected_keywords_count=len(constraints.expected_keywords),
        optional_keywords_count=len(constraints.optional_keywords),
        expected_keywords_hit_count=int(result.expected_keywords_hit_count or 0),
        expected_keyword_hit_evidence_count=int(
            result.expected_keyword_hit_evidence_count or 0,
        ),
        missing_expected_keywords=result.missing_expected_keywords,
        negative_keywords_count=len(constraints.negative_keywords),
        negative_keyword_hit_count=int(result.negative_keyword_hit_count or 0),
        evidence_keyword_hit_counts=dict(result.evidence_keyword_hit_counts),
        relevance_score=final_relevance_score,
        source_coverage_score=source_coverage_score,
        keyword_relevance_score=keyword_relevance_score,
        plan_relevance_score=plan_relevance_score,
        final_relevance_score=final_relevance_score,
        minimum_relevance_threshold=options.minimum_relevance_score,
        relevance_policy=options.relevance_policy,
        relevance_policy_passed=relevance_policy_passed,
        insufficient_evidence_reason=insufficient_reason,
        keyword_policy=constraints.keyword_policy,
        retrieval_passed_keyword_policy=retrieval_passed_keyword_policy,
        plan_metadata=constraints.plan_metadata,
        retrieval_repair_count=repair_diagnostics.count,
        repair_attempted=repair_diagnostics.attempted,
        repair_improved=repair_diagnostics.improved,
        repair_reason=repair_diagnostics.reason,
        pre_repair_usable_evidence_count=repair_diagnostics.pre_usable_count,
        post_repair_usable_evidence_count=repair_diagnostics.post_usable_count,
        repair_query_count=repair_diagnostics.repair_query_count,
        repair_queries_created_count=repair_diagnostics.repair_queries_created_count,
        repair_specific_query_count=repair_diagnostics.repair_specific_query_count,
        repair_generic_query_count=repair_diagnostics.repair_generic_query_count,
        repair_used_specific_concepts=repair_diagnostics.repair_used_specific_concepts,
        repair_used_main_entities=repair_diagnostics.repair_used_main_entities,
        leader_rerank_used=bool(options.leader_rerank and constraints.plan is not None),
        relevance_judged=bool(result.plan_relevance_judged),
        average_plan_relevance_score=result.plan_average_relevance_score,
        plan_relevance_should_use_count=int(result.plan_relevance_should_use_count or 0),
        plan_relevance_specificity_counts=dict(result.plan_relevance_specificity_counts),
        relevance_scores=tuple(dict(score) for score in result.plan_relevance_scores)
        if options.show_relevance
        else (),
        semantic_enabled=bool(result.semantic_enabled),
        semantic_model=result.semantic_model,
        semantic_embedding_model_id=result.semantic_embedding_model_id,
        semantic_candidate_count=int(result.semantic_candidate_count or 0),
        semantic_top_k=result.semantic_top_k,
        semantic_weight=float(result.semantic_weight or 0.0),
        reranker=result.reranker,
        reranker_model_id=result.reranker_model_id,
        reranked_candidate_count=int(result.reranked_candidate_count or 0),
        evaluation_focus=question.evaluation_focus,
        manual_ratings=_manual_rating_placeholders(),
        safe_snippets=_truncate_snippets(keyword_snippets, options.snippet_chars)
        if options.show_snippets
        else (),
    )


def _summary_from_results(results: tuple[GoldenQuestionResult, ...]) -> GoldenEvalSummary:
    confidences = [result.confidence for result in results if result.confidence is not None]
    average_confidence = None
    if confidences:
        average_confidence = round(sum(confidences) / len(confidences), 4)
    return GoldenEvalSummary(
        question_count=len(results),
        retrieval_succeeded_count=sum(1 for result in results if result.retrieval_succeeded),
        answer_succeeded_count=sum(1 for result in results if result.answer_succeeded),
        answer_validation_error_count=sum(
            1 for result in results if result.answer_validation_error is not None
        ),
        retry_used_count=sum(1 for result in results if result.json_retry_used),
        retry_success_count=sum(1 for result in results if result.json_retry_succeeded),
        average_confidence=average_confidence,
        privacy_safe_output_count=sum(1 for result in results if result.privacy_safe_output),
    )


def _format_question_markdown(result: GoldenQuestionResult) -> list[str]:
    lines = [
        f"### {result.question_id}",
        "",
        f"- category: {result.category}",
        f"- retrieval_succeeded: {str(result.retrieval_succeeded).lower()}",
        f"- answer_succeeded: {str(result.answer_succeeded).lower()}",
        f"- evidence_count: {result.evidence_count}",
        f"- candidate_retrieval_succeeded: {str(result.candidate_retrieval_succeeded).lower()}",
        f"- usable_evidence_succeeded: {str(result.usable_evidence_succeeded).lower()}",
        f"- usable_evidence_count: {result.usable_evidence_count}",
        f"- unusable_evidence_count: {result.unusable_evidence_count}",
        f"- should_use_evidence_count: {result.should_use_evidence_count}",
        "- evidence_ids: " + (", ".join(result.evidence_ids) if result.evidence_ids else "none"),
        "- evidence_source_counts: " + _format_counts(result.evidence_source_counts),
        "- used_sources: " + (", ".join(result.used_sources) if result.used_sources else "none"),
        f"- evidence_reference_count: {result.evidence_reference_count}",
        f"- unknown_evidence_reference_count: {result.unknown_evidence_reference_count}",
        f"- confidence: {result.confidence}",
        f"- unknowns_count: {result.unknowns_count}",
        f"- json_retry_used: {str(result.json_retry_used).lower()}",
        f"- json_retry_succeeded: {str(result.json_retry_succeeded).lower()}",
        f"- answer_validation_error: {result.answer_validation_error or 'none'}",
        f"- privacy_safe_output: {str(result.privacy_safe_output).lower()}",
        f"- requested_sources: {_format_tuple(result.requested_sources)}",
        f"- expected_sources: {_format_tuple(result.expected_sources)}",
        f"- required_sources: {_format_tuple(result.required_sources)}",
        f"- preferred_sources: {_format_tuple(result.preferred_sources)}",
        f"- excluded_sources: {_format_tuple(result.excluded_sources)}",
        f"- missing_expected_sources: {_format_tuple(result.missing_expected_sources)}",
        f"- missing_required_sources: {_format_tuple(result.missing_required_sources)}",
        f"- excluded_source_violations: {_format_tuple(result.excluded_source_violations)}",
        f"- source_policy: {result.source_policy}",
        f"- retrieval_passed_source_policy: {str(result.retrieval_passed_source_policy).lower()}",
        f"- expected_keywords_count: {result.expected_keywords_count}",
        f"- optional_keywords_count: {result.optional_keywords_count}",
        f"- expected_keywords_hit_count: {result.expected_keywords_hit_count}",
        f"- expected_keyword_hit_evidence_count: {result.expected_keyword_hit_evidence_count}",
        f"- missing_expected_keywords: {_format_tuple(result.missing_expected_keywords)}",
        f"- negative_keywords_count: {result.negative_keywords_count}",
        f"- negative_keyword_hit_count: {result.negative_keyword_hit_count}",
        f"- evidence_keyword_hit_counts: {_format_counts(result.evidence_keyword_hit_counts)}",
        f"- evidence_relevance_score: {result.relevance_score}",
        f"- source_coverage_score: {result.source_coverage_score}",
        f"- keyword_relevance_score: {result.keyword_relevance_score}",
        f"- plan_relevance_score: {result.plan_relevance_score}",
        f"- final_relevance_score: {result.final_relevance_score}",
        f"- minimum_relevance_threshold: {result.minimum_relevance_threshold}",
        f"- relevance_policy: {result.relevance_policy}",
        f"- relevance_policy_passed: {str(result.relevance_policy_passed).lower()}",
        f"- insufficient_evidence_reason: {result.insufficient_evidence_reason or 'none'}",
        f"- keyword_policy: {result.keyword_policy}",
        f"- retrieval_passed_keyword_policy: {str(result.retrieval_passed_keyword_policy).lower()}",
        f"- plan_created: {str(result.plan_metadata.plan_created).lower()}",
        f"- retrieval_query_count: {result.plan_metadata.retrieval_query_count}",
        f"- main_entity_count: {result.plan_metadata.main_entity_count}",
        f"- specific_concept_count: {result.plan_metadata.specific_concept_count}",
        f"- generic_concept_count: {result.plan_metadata.generic_concept_count}",
        f"- plan_source_preferences: {_format_tuple(result.plan_metadata.source_preferences)}",
        f"- plan_source_constraints: {_format_tuple(result.plan_metadata.source_constraints)}",
        f"- evidence_acceptance_criteria_count: {result.plan_metadata.evidence_acceptance_criteria_count}",
        f"- retrieval_repair_count: {result.retrieval_repair_count}",
        f"- repair_attempted: {str(result.repair_attempted).lower()}",
        f"- repair_improved: {str(result.repair_improved).lower()}",
        f"- repair_reason: {result.repair_reason or 'none'}",
        f"- pre_repair_usable_evidence_count: {result.pre_repair_usable_evidence_count}",
        f"- post_repair_usable_evidence_count: {result.post_repair_usable_evidence_count}",
        f"- repair_query_count: {result.repair_query_count}",
        f"- repair_queries_created_count: {result.repair_queries_created_count}",
        f"- repair_specific_query_count: {result.repair_specific_query_count}",
        f"- repair_generic_query_count: {result.repair_generic_query_count}",
        f"- repair_used_specific_concepts: {str(result.repair_used_specific_concepts).lower()}",
        f"- repair_used_main_entities: {str(result.repair_used_main_entities).lower()}",
        f"- leader_rerank_used: {str(result.leader_rerank_used).lower()}",
        f"- relevance_judged: {str(result.relevance_judged).lower()}",
        f"- average_plan_relevance_score: {result.average_plan_relevance_score}",
        f"- plan_relevance_should_use_count: {result.plan_relevance_should_use_count}",
        "- plan_relevance_specificity_counts: "
        + _format_counts(result.plan_relevance_specificity_counts),
        f"- semantic_enabled: {str(result.semantic_enabled).lower()}",
        f"- semantic_model: {result.semantic_model}",
        f"- semantic_embedding_model_id: {result.semantic_embedding_model_id or 'none'}",
        f"- semantic_candidate_count: {result.semantic_candidate_count}",
        f"- semantic_top_k: {result.semantic_top_k}",
        f"- semantic_weight: {result.semantic_weight}",
        f"- reranker: {result.reranker}",
        f"- reranker_model_id: {result.reranker_model_id or 'none'}",
        f"- reranked_candidate_count: {result.reranked_candidate_count}",
        f"- evaluation_focus: {_format_tuple(result.evaluation_focus)}",
    ]
    if result.plan_metadata.plan is not None:
        lines.extend(
            [
                "",
                "#### Retrieval Plan",
                "",
                "```json",
                json.dumps(result.plan_metadata.plan, ensure_ascii=False, indent=2, sort_keys=True),
                "```",
            ],
        )
    if result.relevance_scores:
        lines.extend(["", "#### Relevance Scores", ""])
        lines.extend(
            (
                f"- {item.get('evidence_id', 'unknown')}: "
                f"score={item.get('relevance_score')}; "
                f"specificity={item.get('specificity')}; "
                f"should_use={item.get('should_use')}; "
                f"reason={item.get('reason_category')}"
            )
            for item in result.relevance_scores
        )
    if result.answer_conclusion is not None:
        lines.extend(
            [
                "",
                "#### Answer",
                "",
                f"- conclusion: {result.answer_conclusion}",
                "- unknowns: "
                + (" | ".join(result.answer_unknowns) if result.answer_unknowns else "none"),
            ],
        )
    if result.safe_snippets:
        lines.extend(["", "#### Snippets", ""])
        lines.extend(
            (
                f"- {item.get('evidence_id', 'unknown')}: "
                f"{item.get('snippet', '')}"
                + (
                    f" [matched_keywords={item.get('matched_keywords')}]"
                    if item.get("matched_keywords")
                    else ""
                )
            )
            for item in result.safe_snippets
        )
    lines.extend(
        [
            "",
            "#### Manual Ratings",
            "",
            "- answer_correctness: ",
            "- evidence_relevance: ",
            "- source_coverage: ",
            "- uncertainty_handling: ",
            "- privacy_safety: ",
            "- source_policy_passed: ",
            "- evidence_relevance_score: ",
            "- expected_keywords_hit_count: ",
            "- missing_expected_keywords: ",
            "- usable_evidence_notes: ",
            "- repair_notes: ",
            "- source_mismatch_notes: ",
            "- irrelevant_evidence_notes: ",
            "- notes: ",
            "",
        ],
    )
    return lines


def _resolve_golden_questions_path(
    config_dir: Path | str | None,
    *,
    questions_config: Path | str | None,
) -> Path:
    if questions_config is not None:
        return Path(questions_config).expanduser()
    config_root = Path(config_dir).expanduser() if config_dir else PROJECT_ROOT / "configs"
    local = config_root / LOCAL_GOLDEN_QUESTIONS_FILENAME
    return local if local.exists() else config_root / DEFAULT_GOLDEN_QUESTIONS_FILENAME


def _load_golden_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    parsed_list = _parse_question_list_yaml(text)
    if parsed_list is not None:
        return parsed_list
    try:
        return _parse_simple_yaml(text)
    except ConfigError:
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ConfigError("unsupported golden question YAML shape") from exc
        loaded = yaml.safe_load(text)
        if not isinstance(loaded, dict):
            raise ConfigError("golden question YAML must be a mapping")
        return loaded


def _parse_questions(value: object) -> list[GoldenQuestion]:
    items: list[tuple[str, Any]]
    if isinstance(value, dict):
        items = list(value.items())
    elif isinstance(value, list):
        items = [
            (str(item.get("id") or f"question_{index}"), item)
            for index, item in enumerate(value, 1)
            if isinstance(item, dict)
        ]
    else:
        return []
    questions: list[GoldenQuestion] = []
    for key, raw in items:
        if not isinstance(raw, dict):
            continue
        question_id = str(raw.get("id") or raw.get("question_id") or key).strip()
        text = str(raw.get("text") or raw.get("question") or "").strip()
        if not question_id or not text:
            continue
        sources = _parse_sources(raw.get("sources"))
        expected_sources = _parse_sources(raw.get("expected_sources"))
        required_sources = _parse_sources(raw.get("required_sources"))
        preferred_sources = _parse_sources(raw.get("preferred_sources"))
        excluded_sources = _parse_sources(raw.get("excluded_sources"))
        category = str(raw.get("category") or "general").strip() or "general"
        source_policy = _optional_source_policy(raw.get("source_policy"))
        questions.append(
            GoldenQuestion(
                question_id=question_id,
                text=text,
                sources=sources,
                category=category,
                expected_sources=expected_sources,
                required_sources=required_sources,
                preferred_sources=preferred_sources,
                excluded_sources=excluded_sources,
                expected_keywords=_parse_string_list(raw.get("expected_keywords")),
                optional_keywords=_parse_string_list(raw.get("optional_keywords")),
                negative_keywords=_parse_string_list(raw.get("negative_keywords")),
                evaluation_focus=_parse_string_list(raw.get("evaluation_focus")),
                source_policy=source_policy,
            ),
        )
    return questions


def _select_golden_questions(
    questions: tuple[GoldenQuestion, ...],
    *,
    query_id: str | None,
    query_limit: int | None,
) -> tuple[GoldenQuestion, ...]:
    selected = questions
    if query_id is not None:
        selected = tuple(question for question in selected if question.question_id == query_id)
        if not selected:
            raise ValueError("configured golden question id was not found")
    if query_limit is not None:
        if query_limit <= 0:
            raise ValueError("query_limit must be positive")
        selected = selected[:query_limit]
    if not selected:
        raise ValueError("no golden questions selected")
    return selected


def _parse_sources(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw_sources = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw_sources = [str(part).strip() for part in value]
    else:
        raw_sources = [str(value).strip()]
    sources = tuple(source for source in raw_sources if source)
    unknown = set(sources) - _SUPPORTED_GOLDEN_SOURCES
    if unknown:
        raise ValueError(f"unsupported golden question sources: {sorted(unknown)}")
    return sources


def _parse_string_list(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        if "," in value:
            parts = value.split(",")
        else:
            parts = [value]
    elif isinstance(value, (list, tuple, set)):
        parts = [str(part) for part in value]
    else:
        parts = [str(value)]
    return tuple(part.strip() for part in parts if part.strip())


def _optional_source_policy(value: object) -> str | None:
    if value is None:
        return None
    policy = str(value).strip().lower()
    if not policy:
        return None
    if policy not in {"soft", "strict"}:
        raise ValueError("source_policy must be soft or strict")
    return policy


def _parse_question_list_yaml(text: str) -> dict[str, Any] | None:
    in_questions = False
    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    pending_list_key: str | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "questions:":
            in_questions = True
            continue
        if not in_questions:
            continue
        if stripped.startswith("- "):
            value = stripped[2:].strip()
            if pending_list_key and current is not None and ":" not in value:
                current.setdefault(pending_list_key, []).append(_yaml_scalar(value))
                continue
            current = {}
            items.append(current)
            pending_list_key = None
            if value and ":" in value:
                key, _, raw_value = value.partition(":")
                current[key.strip()] = _yaml_scalar(raw_value.strip())
            continue
        if current is None or ":" not in stripped:
            continue
        key, _, raw_value = stripped.partition(":")
        clean_key = key.strip()
        clean_value = raw_value.strip()
        if clean_value:
            current[clean_key] = _yaml_scalar(clean_value)
            pending_list_key = None
        else:
            current[clean_key] = []
            pending_list_key = clean_key
    if not items:
        return None
    return {"questions": items}


def _yaml_scalar(value: str) -> Any:
    normalized = value.strip()
    if not normalized:
        return ""
    if normalized.startswith("[") and normalized.endswith("]"):
        inner = normalized[1:-1].strip()
        if not inner:
            return []
        return [_yaml_scalar(part.strip()) for part in inner.split(",")]
    if (
        len(normalized) >= 2
        and normalized[0] == normalized[-1]
        and normalized[0] in {"'", '"'}
    ):
        return normalized[1:-1]
    return normalized


def _resolve_golden_mode(options: GoldenEvalOptions) -> str:
    if options.retrieval_only:
        return "retrieval_only"
    if options.real_model:
        return "real_model"
    return "fake_model"


def _manual_rating_placeholders() -> dict[str, Any]:
    return {
        field: "" if field == "notes" else None
        for field in _MANUAL_RATING_FIELDS
    }


def _unique_sources(values: tuple[str, ...]) -> tuple[str, ...]:
    unknown = set(values) - _SUPPORTED_GOLDEN_SOURCES
    if unknown:
        raise ValueError(f"unsupported golden question sources: {sorted(unknown)}")
    return tuple(dict.fromkeys(source for source in values if source))


def _unique_strings(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


def _expanded_retrieval_text(
    question_text: str,
    *,
    expected_keywords: tuple[str, ...],
    optional_keywords: tuple[str, ...],
    plan: RetrievalPlan | None = None,
    repair: bool = False,
) -> str:
    plan_queries: tuple[str, ...] = ()
    if plan is not None and plan.retrieval_queries:
        plan_queries = plan.retrieval_queries if repair else plan.retrieval_queries[:1]
    plan_concepts = ()
    if plan is not None and repair:
        plan_concepts = plan.specific_concepts + plan.main_entities
        if plan_concepts:
            return " ".join(_unique_strings(plan_concepts)).strip()
        generic_terms = _normalized_set(plan.generic_concepts)
        plan_queries = tuple(
            query
            for query in plan_queries
            if normalize_text(query) not in generic_terms
        )
    keywords = _unique_strings(expected_keywords + optional_keywords + plan_queries + plan_concepts)
    if not keywords:
        return question_text
    return " ".join((question_text, *keywords)).strip()


def _snippets_with_keyword_labels(
    snippets: tuple[dict[str, str], ...],
    *,
    expected_keywords: tuple[str, ...],
    optional_keywords: tuple[str, ...],
) -> tuple[dict[str, str], ...]:
    keywords = _unique_strings(expected_keywords + optional_keywords)
    if not snippets or not keywords:
        return snippets
    enriched: list[dict[str, str]] = []
    normalized_pairs = tuple((keyword, normalize_text(keyword)) for keyword in keywords)
    for item in snippets:
        normalized_snippet = normalize_text(str(item.get("snippet") or ""))
        matched = tuple(
            keyword
            for keyword, normalized_keyword in normalized_pairs
            if normalized_keyword and normalized_keyword in normalized_snippet
        )
        enriched_item = dict(item)
        if matched:
            enriched_item["matched_keywords"] = ",".join(matched)
        enriched.append(enriched_item)
    return tuple(enriched)


def _usable_evidence_count_from_e2e(
    result: Any,
    *,
    minimum_relevance_score: float,
) -> int:
    evidence_count = int(result.evidence_count or 0)
    if evidence_count <= 0:
        return 0
    if not result.plan_relevance_judged:
        return evidence_count
    detailed_scores = tuple(getattr(result, "plan_relevance_scores", ()) or ())
    if detailed_scores:
        return sum(
            1
            for score in detailed_scores
            if bool(score.get("should_use"))
            and float(score.get("relevance_score") or 0.0) >= minimum_relevance_score
        )
    return int(result.plan_relevance_should_use_count or 0)


def _source_coverage_score(
    *,
    retrieval_succeeded: bool,
    source_policy_passed: bool,
    expected_sources: tuple[str, ...],
    actual_sources: set[str],
) -> float:
    if not retrieval_succeeded:
        return 0.0
    if expected_sources:
        ratio = len(set(expected_sources) & actual_sources) / len(set(expected_sources))
    else:
        ratio = 1.0
    if not source_policy_passed:
        ratio *= 0.5
    return round(max(0.0, min(1.0, ratio)), 4)


def _keyword_relevance_score(
    *,
    retrieval_succeeded: bool,
    expected_keyword_count: int,
    expected_keyword_hit_count: int,
    negative_keyword_hit_count: int,
) -> float:
    if not retrieval_succeeded:
        return 0.0
    if expected_keyword_count:
        score = min(1.0, expected_keyword_hit_count / expected_keyword_count)
    else:
        score = 1.0
    score -= min(0.5, 0.2 * negative_keyword_hit_count)
    return round(max(0.0, min(1.0, score)), 4)


def _plan_relevance_score_from_e2e(
    result: Any,
    *,
    minimum_relevance_score: float,
) -> float | None:
    if not result.plan_relevance_judged:
        return None
    usable_count = _usable_evidence_count_from_e2e(
        result,
        minimum_relevance_score=minimum_relevance_score,
    )
    average = result.plan_average_relevance_score
    if average is None:
        return 0.0
    if usable_count == 0:
        return round(min(float(average), minimum_relevance_score * 0.5), 4)
    return round(max(0.0, min(1.0, float(average))), 4)


def _final_relevance_score(
    *,
    retrieval_succeeded: bool,
    evidence_count: int,
    source_coverage_score: float,
    keyword_relevance_score: float,
    plan_relevance_score: float | None,
    should_use_evidence_count: int,
    plan_relevance_judged: bool,
    limit: int,
) -> float:
    if not retrieval_succeeded or evidence_count <= 0:
        return 0.0
    evidence_ratio = min(1.0, evidence_count / max(1, limit))
    if plan_relevance_judged:
        if should_use_evidence_count <= 0:
            return round(min(float(plan_relevance_score or 0.0), 0.35), 4)
        score = (
            0.20 * source_coverage_score
            + 0.20 * keyword_relevance_score
            + 0.50 * float(plan_relevance_score or 0.0)
            + 0.10 * evidence_ratio
        )
    else:
        score = (
            0.35 * source_coverage_score
            + 0.45 * keyword_relevance_score
            + 0.20 * evidence_ratio
        )
    return round(max(0.0, min(1.0, score)), 4)


def _relevance_policy_passed(
    *,
    options: GoldenEvalOptions,
    usable_evidence_succeeded: bool,
    final_relevance_score: float,
    plan_relevance_judged: bool,
) -> bool:
    if options.relevance_policy == "soft" and not options.require_usable_evidence:
        return True
    if options.require_usable_evidence and not usable_evidence_succeeded:
        return False
    if options.relevance_policy == "strict" and plan_relevance_judged:
        return usable_evidence_succeeded and final_relevance_score >= options.minimum_relevance_score
    if options.relevance_policy == "strict":
        return final_relevance_score >= options.minimum_relevance_score
    return True


def _insufficient_evidence_reason(
    result: Any,
    *,
    usable_evidence_succeeded: bool,
    final_relevance_score: float,
    minimum_relevance_score: float,
) -> str | None:
    if usable_evidence_succeeded:
        return None
    if result.evidence_count <= 0:
        return "no candidate evidence was retrieved"
    if result.plan_relevance_judged and int(result.plan_relevance_should_use_count or 0) == 0:
        return "candidate evidence was found, but relevance judge found no usable evidence"
    if final_relevance_score < minimum_relevance_score:
        return "candidate evidence was found, but final relevance is below threshold"
    return None


def _relevance_score(
    *,
    retrieval_succeeded: bool,
    evidence_count: int,
    source_policy_passed: bool,
    expected_sources: tuple[str, ...],
    actual_sources: set[str],
    expected_keyword_count: int,
    expected_keyword_hit_count: int,
    negative_keyword_hit_count: int,
    limit: int,
) -> float:
    if not retrieval_succeeded or evidence_count <= 0:
        return 0.0
    source_ratio = 1.0
    if expected_sources:
        source_ratio = len(set(expected_sources) & actual_sources) / len(set(expected_sources))
    keyword_ratio = 1.0
    if expected_keyword_count:
        keyword_ratio = min(1.0, expected_keyword_hit_count / expected_keyword_count)
    evidence_ratio = min(1.0, evidence_count / max(1, limit))
    score = (
        0.20
        + (0.20 if source_policy_passed else 0.0)
        + (0.20 * source_ratio)
        + (0.30 * keyword_ratio)
        + (0.10 * evidence_ratio)
    )
    penalty = min(0.20, 0.10 * negative_keyword_hit_count)
    return round(max(0.0, min(1.0, score - penalty)), 4)


def _truncate_snippets(
    snippets: tuple[dict[str, str], ...],
    max_chars: int,
) -> tuple[dict[str, str], ...]:
    if max_chars <= 0:
        raise ValueError("snippet_chars must be positive")
    truncated: list[dict[str, str]] = []
    for item in snippets:
        snippet = str(item.get("snippet", ""))
        if len(snippet) > max_chars:
            snippet = snippet[: max(0, max_chars - 3)].rstrip() + "..."
        redacted = dict(item)
        redacted["snippet"] = snippet
        truncated.append(redacted)
    return tuple(truncated)


def _safe_config_path(path: Path) -> str:
    name = path.name
    if name.endswith(".local.yaml"):
        return name
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return name


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}:{counts[key]}" for key in sorted(counts))


def _format_tuple(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "none"


def _write_text(path: Path | str | None, text: str) -> Path | None:
    if path is None:
        return None
    resolved = Path(path).expanduser()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(text, encoding="utf-8")
    return resolved


def _default_golden_questions() -> tuple[GoldenQuestion, ...]:
    return (
        GoldenQuestion(
            question_id="research_notes",
            category="research",
            text="研究に関係するメモやLINEの記録を探してください。",
            sources=("line", "notes"),
            expected_sources=("line", "notes"),
            preferred_sources=("notes",),
            expected_keywords=("研究",),
            optional_keywords=("予定", "準備"),
            evaluation_focus=("evidence_relevance", "source_coverage"),
        ),
        GoldenQuestion(
            question_id="outing_photos",
            category="photos",
            text="外出や屋外に関係しそうな写真の記録を探してください。",
            sources=("photos",),
            expected_sources=("photos",),
            expected_keywords=("外出",),
            optional_keywords=("屋外",),
            evaluation_focus=("evidence_relevance",),
        ),
        GoldenQuestion(
            question_id="insufficient_evidence",
            category="safety",
            text="根拠が足りない場合は不明と答えてください。",
            sources=("photos", "line", "notes"),
            expected_sources=("photos", "line", "notes"),
            evaluation_focus=("uncertainty_handling",),
        ),
    )
