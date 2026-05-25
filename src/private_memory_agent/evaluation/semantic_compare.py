"""Compare semantic retrieval configurations with privacy-safe quality metrics."""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from private_memory_agent.agent import RetrievalPlanner
from private_memory_agent.e2e import DEFAULT_E2E_DB_PATH, DEFAULT_E2E_QUERY_LIMIT
from private_memory_agent.evaluation.golden import (
    GoldenEvalOptions,
    GoldenQuestionResult,
    run_golden_eval,
)

DEFAULT_REAL_SEMANTIC_MODEL = "ruri-v3-310m"
DEFAULT_REAL_RERANKER = "ruri-v3-reranker-310m"


@dataclass(frozen=True)
class EmbeddingDeviceStatus:
    """Privacy-safe embedding device diagnostics."""

    requested_device: str = "auto"
    selected_device: str = "cpu"
    cuda_available: bool | None = None
    cuda_warning_detected: bool = False
    torch_available: bool | None = None
    recommendation: str = "Use --embedding-device cpu if CUDA emits driver warnings."

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_device": self.requested_device,
            "selected_device": self.selected_device,
            "cuda_available": self.cuda_available,
            "cuda_warning_detected": self.cuda_warning_detected,
            "torch_available": self.torch_available,
            "recommendation": self.recommendation,
        }


@dataclass(frozen=True)
class SemanticCompareOptions:
    """Options for semantic retrieval quality comparison."""

    config_dir: Path | str | None = None
    paths_config: Path | str | None = None
    db_path: Path | str = DEFAULT_E2E_DB_PATH
    questions_config: Path | str | None = None
    query_limit: int | None = None
    query_id: str | None = None
    limit: int = DEFAULT_E2E_QUERY_LIMIT
    real_semantic_model: str = DEFAULT_REAL_SEMANTIC_MODEL
    real_reranker: str = DEFAULT_REAL_RERANKER
    semantic_top_k: int | None = 20
    semantic_weight: float = 1.0
    rerank_top_k: int | None = 20
    retrieval_repair: int = 1
    minimum_relevance_score: float = 0.6
    embedding_device: str = "auto"
    show_relevance: bool = False
    retrieval_planner: RetrievalPlanner | None = None
    model_key: str = "leader"
    allow_remote: bool = False


@dataclass(frozen=True)
class SemanticCompareQueryResult:
    """Per-query, per-configuration comparison metrics."""

    question_id: str
    candidate_retrieval_succeeded: bool
    evidence_count: int
    semantic_candidate_count: int
    reranked_candidate_count: int
    usable_evidence_succeeded: bool
    usable_evidence_count: int
    should_use_evidence_count: int
    average_plan_relevance_score: float | None
    final_relevance_score: float
    relevance_policy_passed: bool
    strict_passed: bool
    evidence_source_counts: dict[str, int] = field(default_factory=dict)
    repair_count: int = 0
    repair_improved: bool = False
    quality_judged: bool = False
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "candidate_retrieval_succeeded": self.candidate_retrieval_succeeded,
            "evidence_count": self.evidence_count,
            "semantic_candidate_count": self.semantic_candidate_count,
            "reranked_candidate_count": self.reranked_candidate_count,
            "usable_evidence_succeeded": self.usable_evidence_succeeded,
            "usable_evidence_count": self.usable_evidence_count,
            "should_use_evidence_count": self.should_use_evidence_count,
            "average_plan_relevance_score": self.average_plan_relevance_score,
            "final_relevance_score": self.final_relevance_score,
            "relevance_policy_passed": self.relevance_policy_passed,
            "strict_passed": self.strict_passed,
            "evidence_source_counts": dict(self.evidence_source_counts),
            "repair_count": self.repair_count,
            "repair_improved": self.repair_improved,
            "quality_judged": self.quality_judged,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class SemanticCompareConfigResult:
    """One retrieval configuration's aggregate comparison result."""

    config_id: str
    semantic_model: str
    reranker: str
    leader_plan: bool
    leader_rerank: bool
    quality_judged: bool
    query_results: tuple[SemanticCompareQueryResult, ...] = ()
    error_class: str | None = None
    error_message: str | None = None
    warnings: tuple[str, ...] = ()

    @property
    def strict_passed_count(self) -> int:
        return sum(1 for item in self.query_results if item.strict_passed)

    @property
    def usable_evidence_count(self) -> int:
        return sum(item.usable_evidence_count for item in self.query_results)

    @property
    def average_final_relevance_score(self) -> float | None:
        if not self.query_results:
            return None
        return round(
            sum(item.final_relevance_score for item in self.query_results)
            / len(self.query_results),
            4,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id,
            "semantic_model": self.semantic_model,
            "reranker": self.reranker,
            "leader_plan": self.leader_plan,
            "leader_rerank": self.leader_rerank,
            "quality_judged": self.quality_judged,
            "strict_passed_count": self.strict_passed_count,
            "usable_evidence_count": self.usable_evidence_count,
            "average_final_relevance_score": self.average_final_relevance_score,
            "query_results": [item.to_dict() for item in self.query_results],
            "error_class": self.error_class,
            "error_message": self.error_message,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class SemanticCompareReport:
    """Top-level semantic comparison report."""

    ok: bool
    config_results: tuple[SemanticCompareConfigResult, ...]
    recommended_config_id: str | None
    recommendation_reason: str
    embedding_device_status: EmbeddingDeviceStatus
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "recommended_config_id": self.recommended_config_id,
            "recommendation_reason": self.recommendation_reason,
            "embedding_device_status": self.embedding_device_status.to_dict(),
            "config_results": [item.to_dict() for item in self.config_results],
            "warnings": list(self.warnings),
            "privacy": {
                "default_output": (
                    "configuration ids, source counts, evidence counts, quality booleans, "
                    "scores, and safe diagnostics only"
                ),
                "hidden_by_default": (
                    "question text, snippets, answer text, filenames, paths, GPS, EXIF, "
                    "OCR, LINE text, note bodies, captions, raw plans, raw model output"
                ),
            },
        }


@dataclass(frozen=True)
class _CompareConfig:
    config_id: str
    semantic_model: str
    reranker: str = "none"
    leader_plan: bool = False
    leader_rerank: bool = False


def run_semantic_compare(options: SemanticCompareOptions) -> SemanticCompareReport:
    """Compare retrieval configurations through golden evaluation."""

    if options.embedding_device not in {"auto", "cpu", "cuda"}:
        raise ValueError("embedding_device must be auto, cpu, or cuda")
    if options.semantic_top_k is not None and options.semantic_top_k <= 0:
        raise ValueError("semantic_top_k must be positive")
    if options.rerank_top_k is not None and options.rerank_top_k <= 0:
        raise ValueError("rerank_top_k must be positive")

    device_status = inspect_embedding_device(options.embedding_device)
    results = tuple(_run_compare_config(config, options) for config in _compare_configs(options))
    recommended = _select_recommended_config(results)
    warnings_list = [
        warning
        for result in results
        for warning in result.warnings
    ]
    if recommended is None:
        reason = "No compared configuration produced judged usable evidence."
    else:
        reason = (
            "Recommended by strict pass count, usable evidence count, final relevance, "
            "and privacy-safe operation."
        )
    return SemanticCompareReport(
        ok=recommended is not None,
        config_results=results,
        recommended_config_id=None if recommended is None else recommended.config_id,
        recommendation_reason=reason,
        embedding_device_status=device_status,
        warnings=tuple(dict.fromkeys(warnings_list)),
    )


def report_to_json(report: SemanticCompareReport) -> str:
    """Serialize a semantic comparison report."""

    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def format_semantic_compare_report(report: SemanticCompareReport) -> str:
    """Format a privacy-safe human-readable comparison report."""

    lines = [
        "# Semantic Retrieval Comparison",
        "",
        f"- status: {'passed' if report.ok else 'needs-attention'}",
        f"- recommended_config: {report.recommended_config_id or 'none'}",
        f"- recommendation_reason: {report.recommendation_reason}",
        "- embedding_device: "
        f"requested={report.embedding_device_status.requested_device}; "
        f"selected={report.embedding_device_status.selected_device}; "
        f"cuda_available={report.embedding_device_status.cuda_available}; "
        f"cuda_warning_detected={report.embedding_device_status.cuda_warning_detected}",
    ]
    if report.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in report.warnings)
    lines.extend(["", "## Configurations", ""])
    for result in report.config_results:
        lines.extend(
            [
                f"### {result.config_id}",
                "",
                f"- semantic_model: {result.semantic_model}",
                f"- reranker: {result.reranker}",
                f"- leader_plan: {str(result.leader_plan).lower()}",
                f"- leader_rerank: {str(result.leader_rerank).lower()}",
                f"- quality_judged: {str(result.quality_judged).lower()}",
                f"- strict_passed_count: {result.strict_passed_count}",
                f"- usable_evidence_count: {result.usable_evidence_count}",
                f"- average_final_relevance_score: {result.average_final_relevance_score}",
            ],
        )
        if result.error_class:
            lines.append(f"- error: {result.error_class}: {result.error_message}")
        for query in result.query_results:
            lines.append(
                "  - "
                f"{query.question_id}: "
                f"candidates={query.evidence_count}; "
                f"semantic={query.semantic_candidate_count}; "
                f"reranked={query.reranked_candidate_count}; "
                f"usable={query.usable_evidence_count}; "
                f"strict_passed={str(query.strict_passed).lower()}; "
                f"final_relevance={query.final_relevance_score}; "
                f"sources={_format_counts(query.evidence_source_counts)}"
            )
    return "\n".join(lines).rstrip() + "\n"


def inspect_embedding_device(requested_device: str) -> EmbeddingDeviceStatus:
    """Inspect embedding device status without requiring GPU availability."""

    requested = requested_device.strip().lower()
    if requested == "cpu":
        return EmbeddingDeviceStatus(
            requested_device=requested,
            selected_device="cpu",
            cuda_available=None,
            torch_available=None,
            recommendation="CPU selected explicitly; CUDA driver warnings are avoided.",
        )
    captured_warnings: list[warnings.WarningMessage] = []
    cuda_available: bool | None = None
    torch_available: bool | None = None
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            import torch

            torch_available = True
            cuda_available = bool(torch.cuda.is_available())
            captured_warnings = list(caught)
    except Exception:
        torch_available = False
        cuda_available = False
    cuda_warning = any(
        "cuda" in str(item.message).lower()
        or "nvidia driver" in str(item.message).lower()
        for item in captured_warnings
    )
    selected = requested
    if requested == "auto":
        selected = "cuda" if cuda_available else "cpu"
    if selected == "cuda" and not cuda_available:
        recommendation = "CUDA was requested but is unavailable; use --embedding-device cpu."
    elif cuda_warning:
        recommendation = (
            "CUDA emitted a warning; use --embedding-device cpu until the driver is fixed."
        )
    elif selected == "cpu":
        recommendation = "CPU is selected; this is slower but stable and local."
    else:
        recommendation = "CUDA appears available; monitor memory and driver compatibility."
    return EmbeddingDeviceStatus(
        requested_device=requested,
        selected_device=selected,
        cuda_available=cuda_available,
        cuda_warning_detected=cuda_warning,
        torch_available=torch_available,
        recommendation=recommendation,
    )


def _compare_configs(options: SemanticCompareOptions) -> tuple[_CompareConfig, ...]:
    real = options.real_semantic_model
    reranker = options.real_reranker
    return (
        _CompareConfig("text_only", "none"),
        _CompareConfig("hash_semantic", "hash"),
        _CompareConfig("ruri_v3_310m", real),
        _CompareConfig("ruri_v3_310m_plus_reranker", real, reranker=reranker),
        _CompareConfig(
            "leader_plan_ruri",
            real,
            leader_plan=True,
            leader_rerank=True,
        ),
        _CompareConfig(
            "leader_plan_ruri_plus_reranker",
            real,
            reranker=reranker,
            leader_plan=True,
            leader_rerank=True,
        ),
    )


def _run_compare_config(
    config: _CompareConfig,
    options: SemanticCompareOptions,
) -> SemanticCompareConfigResult:
    try:
        report = run_golden_eval(
            GoldenEvalOptions(
                config_dir=options.config_dir,
                paths_config=options.paths_config,
                db_path=options.db_path,
                questions_config=options.questions_config,
                retrieval_only=True,
                query_limit=options.query_limit,
                query_id=options.query_id,
                limit=options.limit,
                leader_plan=config.leader_plan,
                leader_rerank=config.leader_rerank,
                retrieval_repair=options.retrieval_repair if config.leader_plan else 0,
                show_relevance=options.show_relevance and config.leader_plan,
                minimum_relevance_score=options.minimum_relevance_score,
                require_usable_evidence=True,
                relevance_policy="strict",
                semantic_model=config.semantic_model,
                semantic_top_k=options.semantic_top_k,
                semantic_weight=options.semantic_weight,
                reranker=config.reranker,
                rerank_top_k=options.rerank_top_k,
                embedding_device=options.embedding_device,
                retrieval_planner=options.retrieval_planner if config.leader_plan else None,
                model_key=options.model_key,
                allow_remote=options.allow_remote,
            ),
        )
    except (RuntimeError, ValueError) as exc:
        return SemanticCompareConfigResult(
            config_id=config.config_id,
            semantic_model=config.semantic_model,
            reranker=config.reranker,
            leader_plan=config.leader_plan,
            leader_rerank=config.leader_rerank,
            quality_judged=False,
            error_class=exc.__class__.__name__,
            error_message=_safe_message(exc),
            warnings=(f"{config.config_id}: comparison failed safely",),
        )
    query_results = tuple(_query_result_from_golden(result) for result in report.results)
    quality_judged = any(item.quality_judged for item in query_results)
    warnings_list = list(report.warnings)
    if any(item.evidence_count > 0 for item in query_results) and not quality_judged:
        warnings_list.append(
            f"{config.config_id}: "
            "configuration retrieved candidates but did not run relevance judging",
        )
    return SemanticCompareConfigResult(
        config_id=config.config_id,
        semantic_model=config.semantic_model,
        reranker=config.reranker,
        leader_plan=config.leader_plan,
        leader_rerank=config.leader_rerank,
        quality_judged=quality_judged,
        query_results=query_results,
        warnings=tuple(dict.fromkeys(warnings_list)),
    )


def _query_result_from_golden(result: GoldenQuestionResult) -> SemanticCompareQueryResult:
    quality_judged = bool(result.relevance_judged)
    displayed_final_relevance = result.final_relevance_score if quality_judged else 0.0
    strict_passed = bool(
        quality_judged
        and result.usable_evidence_succeeded
        and result.relevance_policy_passed
        and displayed_final_relevance >= result.minimum_relevance_threshold
    )
    warnings_list: list[str] = []
    if result.evidence_count > 0 and not quality_judged:
        warnings_list.append("configuration retrieved candidates but did not run relevance judging")
    return SemanticCompareQueryResult(
        question_id=result.question_id,
        candidate_retrieval_succeeded=result.candidate_retrieval_succeeded,
        evidence_count=result.evidence_count,
        semantic_candidate_count=result.semantic_candidate_count,
        reranked_candidate_count=result.reranked_candidate_count,
        usable_evidence_succeeded=result.usable_evidence_succeeded,
        usable_evidence_count=result.usable_evidence_count,
        should_use_evidence_count=result.should_use_evidence_count,
        average_plan_relevance_score=result.average_plan_relevance_score,
        final_relevance_score=displayed_final_relevance,
        relevance_policy_passed=result.relevance_policy_passed,
        strict_passed=strict_passed,
        evidence_source_counts=dict(result.evidence_source_counts),
        repair_count=result.retrieval_repair_count,
        repair_improved=result.repair_improved,
        quality_judged=quality_judged,
        warnings=tuple(warnings_list),
    )


def _select_recommended_config(
    results: tuple[SemanticCompareConfigResult, ...],
) -> SemanticCompareConfigResult | None:
    candidates = [
        result
        for result in results
        if result.error_class is None
        and result.quality_judged
        and result.strict_passed_count > 0
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda result: (
            result.strict_passed_count,
            result.usable_evidence_count,
            result.average_final_relevance_score or 0.0,
            _source_coverage_width(result),
        ),
    )


def _source_coverage_width(result: SemanticCompareConfigResult) -> int:
    sources: set[str] = set()
    for query_result in result.query_results:
        sources.update(
            source
            for source, count in query_result.evidence_source_counts.items()
            if count > 0
        )
    return len(sources)


def _format_counts(values: dict[str, int]) -> str:
    if not values:
        return "none"
    return ",".join(f"{key}:{value}" for key, value in sorted(values.items()))


def _safe_message(exc: BaseException) -> str:
    text = str(exc).strip()
    if not text:
        return exc.__class__.__name__
    return text[:240]
