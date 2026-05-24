"""Golden question evaluation over existing local evidence."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
    negative_keywords_count: int = 0
    evaluation_focus: tuple[str, ...] = ()
    manual_ratings: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return (
            self.retrieval_succeeded
            and self.retrieval_passed_source_policy
            and (self.answer_succeeded or self.confidence is None)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "category": self.category,
            "retrieval_succeeded": self.retrieval_succeeded,
            "answer_succeeded": self.answer_succeeded,
            "evidence_count": self.evidence_count,
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
            "negative_keywords_count": self.negative_keywords_count,
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
    constrained_questions = tuple(
        _constrained_question(question, options)
        for question in questions
    )
    e2e_report = run_e2e_smoke(
        E2ESmokeOptions(
            config_dir=options.config_dir,
            paths_config=options.paths_config,
            db_path=options.db_path,
            queries=tuple(
                E2ESmokeQuery(
                    query_id=question.question_id,
                    text=question.text,
                    sources=constrained.requested_sources,
                )
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
            model_key=options.model_key,
            allow_remote=options.allow_remote,
            no_fallback=True,
        ),
    )
    results = tuple(
        _golden_result_from_e2e(
            question,
            constrained,
            result,
            options=options,
        )
        for question, constrained, result in zip(
            questions,
            constrained_questions,
            e2e_report.query_results,
            strict=False,
        )
    )
    ok = e2e_report.db_exists and bool(results) and all(result.passed for result in results)
    if mode != "retrieval_only":
        ok = ok and all(result.answer_succeeded for result in results)
    return GoldenEvalReport(
        mode=mode,
        ok=ok,
        questions_config_path=_safe_config_path(questions_path),
        db_exists=e2e_report.db_exists,
        summary=_summary_from_results(results),
        results=results,
        warnings=tuple(e2e_report.warnings),
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


def _constrained_question(
    question: GoldenQuestion,
    options: GoldenEvalOptions,
) -> _GoldenSourceConstraints:
    source_policy = (
        "strict"
        if options.source_policy == "strict"
        else (question.source_policy or options.source_policy or "soft")
    ).strip().lower()
    if source_policy not in {"soft", "strict"}:
        raise ValueError("source_policy must be soft or strict")

    expected_sources = _unique_sources(question.expected_sources)
    required_sources = _unique_sources(question.required_sources + options.require_sources)
    preferred_sources = _unique_sources(question.preferred_sources + options.preferred_sources)
    excluded_sources = _unique_sources(question.excluded_sources + options.exclude_sources)

    hard_sources = question.sources or expected_sources or required_sources
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
    )


def _golden_result_from_e2e(
    question: GoldenQuestion,
    constraints: _GoldenSourceConstraints,
    result: Any,
    *,
    options: GoldenEvalOptions,
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
    return GoldenQuestionResult(
        question_id=question.question_id,
        category=question.category,
        retrieval_succeeded=result.retrieval_succeeded,
        answer_succeeded=result.answer_succeeded,
        evidence_count=result.evidence_count,
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
        safe_snippets=_truncate_snippets(result.safe_snippets, options.snippet_chars)
        if options.show_snippets
        else (),
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
        expected_keywords_count=len(question.expected_keywords),
        negative_keywords_count=len(question.negative_keywords),
        evaluation_focus=question.evaluation_focus,
        manual_ratings=_manual_rating_placeholders(),
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
        f"- negative_keywords_count: {result.negative_keywords_count}",
        f"- evaluation_focus: {_format_tuple(result.evaluation_focus)}",
    ]
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
            f"- {item.get('evidence_id', 'unknown')}: {item.get('snippet', '')}"
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
            evaluation_focus=("evidence_relevance", "source_coverage"),
        ),
        GoldenQuestion(
            question_id="outing_photos",
            category="photos",
            text="外出や屋外に関係しそうな写真の記録を探してください。",
            sources=("photos",),
            expected_sources=("photos",),
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
