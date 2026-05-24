"""Privacy-safe real-data E2E smoke workflow."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from private_memory_agent.agent import (
    AnswerValidationError,
    FakeLeaderChatModelClient,
    LeaderAgent,
    PrivacyGuard,
    build_leader_prompt,
    diagnostics_from_error,
)
from private_memory_agent.config import ConfigBundle, load_config
from private_memory_agent.config.loader import ConfigError, _parse_simple_yaml
from private_memory_agent.db_diagnostics import diagnose_retrieval_query, inspect_source_coverage
from private_memory_agent.retrieval import (
    Evidence,
    RetrievalFilters,
    RetrievalResult,
    RetrievalService,
    pack_evidence_for_prompt,
)
from private_memory_agent.runtime import (
    ChatEndpointPreflightResult,
    ModelRuntimeError,
    OpenAICompatibleHTTPClient,
    endpoint_from_model_spec,
    preflight_chat_endpoint,
)

DEFAULT_E2E_DB_PATH = Path("data/local/private_memory_agent.sqlite3")
DEFAULT_E2E_QUERY_LIMIT = 5
DEFAULT_E2E_LEADER_MODEL_KEY = "leader"
DEFAULT_E2E_QUERY_FILENAME = "e2e_smoke_queries.example.yaml"
LOCAL_E2E_QUERY_FILENAME = "e2e_smoke_queries.local.yaml"
SUPPORTED_E2E_SOURCES = {"photos", "line", "notes"}
DEFAULT_E2E_REAL_MODEL_TIMEOUT_SECONDS = 300.0
DEFAULT_E2E_REAL_MODEL_MAX_TOKENS = 256
DEFAULT_E2E_REAL_MODEL_TEMPERATURE = 0.2
DEFAULT_E2E_MAX_EVIDENCE_ITEMS = 3
DEFAULT_E2E_MAX_EVIDENCE_CHARS = 2000
_PATH_LIKE_RE = re.compile(r"(?:/[^\s]+|[A-Za-z]:\\[^\s]+|\\\\[^\s]+)")
_PRECISE_DECIMAL_RE = re.compile(r"\b-?\d{1,3}\.\d{4,}\b")

_DEFAULT_SMOKE_QUERIES = (
    (
        "photos_outing",
        "最近の写真説明から、外出に関係しそうな記録を探してください。",
        ("photos",),
    ),
    (
        "research_records",
        "研究に関係しそうなメモやLINEの記録を探してください。",
        ("line", "notes"),
    ),
    (
        "insufficient_evidence",
        "根拠が足りない場合は不明と答えてください。",
        ("photos", "line", "notes"),
    ),
)


@dataclass(frozen=True)
class E2ESmokeQuery:
    """One configured smoke query.

    Query text is intentionally not exposed in report output because local
    overrides may contain sensitive words.
    """

    query_id: str
    text: str
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class E2ESmokeCounts:
    """Aggregate DB counts safe for display."""

    media_items_count: int = 0
    media_annotations_count: int = 0
    line_messages_count: int = 0
    notes_count: int = 0
    source_items_count: int = 0
    evidence_capable_source_count: int = 0
    available_sources: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "media_items_count": self.media_items_count,
            "media_annotations_count": self.media_annotations_count,
            "line_messages_count": self.line_messages_count,
            "notes_count": self.notes_count,
            "source_items_count": self.source_items_count,
            "evidence_capable_source_count": self.evidence_capable_source_count,
            "available_sources": list(self.available_sources),
        }


@dataclass(frozen=True)
class E2EIndexStatus:
    """Index availability safe for display."""

    text_documents_count: int = 0
    text_index_available: bool = False
    text_fts_available: bool = False
    text_documents_count_kind: str = "unavailable"
    text_documents_table: str | None = None
    text_documents_derived_from: tuple[str, ...] = ()
    text_documents_source_breakdown: dict[str, int] = field(default_factory=dict)
    embeddings_count: int = 0
    embedding_index_available: bool = False
    embeddings_count_kind: str = "unavailable"
    embeddings_derived_from: tuple[str, ...] = ()
    embedding_source_breakdown_available: bool = False
    embedding_source_breakdown: dict[str, int] = field(default_factory=dict)
    vector_index_status: str = "not_checked"
    media_annotations_in_text_index_count: int = 0
    media_annotations_searchable: bool | None = None
    media_annotations_searchable_via: tuple[str, ...] = ()
    photo_evidence_retrievable: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text_documents_count": self.text_documents_count,
            "text_index_available": self.text_index_available,
            "text_fts_available": self.text_fts_available,
            "text_documents_count_kind": self.text_documents_count_kind,
            "text_documents_table": self.text_documents_table,
            "text_documents_derived_from": list(self.text_documents_derived_from),
            "text_documents_source_breakdown": dict(self.text_documents_source_breakdown),
            "embeddings_count": self.embeddings_count,
            "embedding_index_available": self.embedding_index_available,
            "embeddings_count_kind": self.embeddings_count_kind,
            "embeddings_derived_from": list(self.embeddings_derived_from),
            "embedding_source_breakdown_available": self.embedding_source_breakdown_available,
            "embedding_source_breakdown": dict(self.embedding_source_breakdown),
            "vector_index_status": self.vector_index_status,
            "media_annotations_in_text_index_count": self.media_annotations_in_text_index_count,
            "media_annotations_searchable": self.media_annotations_searchable,
            "media_annotations_searchable_via": list(self.media_annotations_searchable_via),
            "photo_evidence_retrievable": self.photo_evidence_retrievable,
        }


@dataclass(frozen=True)
class E2ESourceCoverage:
    """Real-vs-fallback evidence coverage for smoke queries."""

    real_photo_evidence_count: int = 0
    real_line_evidence_count: int = 0
    real_note_evidence_count: int = 0
    fallback_evidence_count: int = 0
    queries_with_zero_evidence: int = 0
    queries_with_only_fallback_evidence: int = 0
    queries_with_mixed_sources: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "real_photo_evidence_count": self.real_photo_evidence_count,
            "real_line_evidence_count": self.real_line_evidence_count,
            "real_note_evidence_count": self.real_note_evidence_count,
            "fallback_evidence_count": self.fallback_evidence_count,
            "queries_with_zero_evidence": self.queries_with_zero_evidence,
            "queries_with_only_fallback_evidence": self.queries_with_only_fallback_evidence,
            "queries_with_mixed_sources": self.queries_with_mixed_sources,
        }


@dataclass(frozen=True)
class E2EAnswerAudit:
    """Aggregate answer quality counters with no answer or evidence payloads."""

    answer_succeeded_count: int = 0
    answer_validation_error_count: int = 0
    retry_used_count: int = 0
    retry_success_count: int = 0
    average_confidence: float | None = None
    evidence_reference_coverage: float | None = None
    unknown_evidence_reference_count: int = 0
    answer_source_counts: dict[str, int] = field(default_factory=dict)
    queries_with_empty_used_sources: int = 0
    queries_with_empty_unknowns: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer_succeeded_count": self.answer_succeeded_count,
            "answer_validation_error_count": self.answer_validation_error_count,
            "retry_used_count": self.retry_used_count,
            "retry_success_count": self.retry_success_count,
            "average_confidence": self.average_confidence,
            "evidence_reference_coverage": self.evidence_reference_coverage,
            "unknown_evidence_reference_count": self.unknown_evidence_reference_count,
            "answer_source_counts": dict(self.answer_source_counts),
            "queries_with_empty_used_sources": self.queries_with_empty_used_sources,
            "queries_with_empty_unknowns": self.queries_with_empty_unknowns,
        }


@dataclass(frozen=True)
class E2ESmokeQueryResult:
    """Privacy-safe per-query smoke status."""

    query_label: str
    sources: tuple[str, ...]
    retrieval_succeeded: bool
    evidence_count: int
    evidence_ids: tuple[str, ...] = ()
    used_inventory_fallback: bool = False
    answer_succeeded: bool = False
    answer_confidence: float | None = None
    answer_evidence_references: tuple[str, ...] = ()
    used_sources: tuple[str, ...] = ()
    answer_conclusion: str | None = None
    answer_unknowns: tuple[str, ...] = ()
    answer_unknown_count: int | None = None
    answer_evidence_reference_count: int | None = None
    answer_used_source_count: int | None = None
    safe_snippets: tuple[dict[str, str], ...] = ()
    evidence_source_counts: dict[str, int] = field(default_factory=dict)
    retrieval_stage_counts: dict[str, int] = field(default_factory=dict)
    source_stage_counts: dict[str, dict[str, Any]] = field(default_factory=dict)
    fallback_reason: str | None = None
    error_class: str | None = None
    error_message: str | None = None
    model_id: str | None = None
    endpoint_url: str | None = None
    timeout_seconds: float | None = None
    max_tokens: int | None = None
    prompt_chars: int | None = None
    evidence_sent_count: int | None = None
    raw_response_chars: int | None = None
    json_extraction_succeeded: bool | None = None
    json_extraction_strategy: str | None = None
    answer_validation_error_class: str | None = None
    answer_validation_error_message: str | None = None
    contains_json_like_object: bool | None = None
    contains_think_tag: bool | None = None
    contains_fenced_json: bool | None = None
    extraction_attempts: int | None = None
    json_retry_used: bool | None = None
    json_retry_succeeded: bool | None = None
    allowed_evidence_count: int | None = None
    allowed_sources: tuple[str, ...] = ()
    raw_model_output_preview: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_label": self.query_label,
            "sources": list(self.sources),
            "retrieval_succeeded": self.retrieval_succeeded,
            "evidence_count": self.evidence_count,
            "evidence_ids": list(self.evidence_ids),
            "used_inventory_fallback": self.used_inventory_fallback,
            "answer_succeeded": self.answer_succeeded,
            "answer_confidence": self.answer_confidence,
            "answer_evidence_references": list(self.answer_evidence_references),
            "used_sources": list(self.used_sources),
            "answer_conclusion": self.answer_conclusion,
            "answer_unknowns": list(self.answer_unknowns),
            "answer_unknown_count": self.answer_unknown_count,
            "answer_evidence_reference_count": self.answer_evidence_reference_count,
            "answer_used_source_count": self.answer_used_source_count,
            "safe_snippets": [dict(item) for item in self.safe_snippets],
            "evidence_source_counts": dict(self.evidence_source_counts),
            "retrieval_stage_counts": dict(self.retrieval_stage_counts),
            "source_stage_counts": {
                source: dict(counts)
                for source, counts in self.source_stage_counts.items()
            },
            "fallback_reason": self.fallback_reason,
            "error_class": self.error_class,
            "error_message": self.error_message,
            "model_id": self.model_id,
            "endpoint_url": self.endpoint_url,
            "timeout_seconds": self.timeout_seconds,
            "max_tokens": self.max_tokens,
            "prompt_chars": self.prompt_chars,
            "evidence_sent_count": self.evidence_sent_count,
            "raw_response_chars": self.raw_response_chars,
            "json_extraction_succeeded": self.json_extraction_succeeded,
            "json_extraction_strategy": self.json_extraction_strategy,
            "answer_validation_error_class": self.answer_validation_error_class,
            "answer_validation_error_message": self.answer_validation_error_message,
            "contains_json_like_object": self.contains_json_like_object,
            "contains_think_tag": self.contains_think_tag,
            "contains_fenced_json": self.contains_fenced_json,
            "extraction_attempts": self.extraction_attempts,
            "json_retry_used": self.json_retry_used,
            "json_retry_succeeded": self.json_retry_succeeded,
            "allowed_evidence_count": self.allowed_evidence_count,
            "allowed_sources": list(self.allowed_sources),
            "raw_model_output_preview": self.raw_model_output_preview,
        }


@dataclass(frozen=True)
class E2ESmokeReport:
    """Top-level smoke result with no private payloads."""

    mode: str
    db_exists: bool
    counts: E2ESmokeCounts = field(default_factory=E2ESmokeCounts)
    indexes: E2EIndexStatus = field(default_factory=E2EIndexStatus)
    query_results: tuple[E2ESmokeQueryResult, ...] = ()
    source_coverage: E2ESourceCoverage = field(default_factory=E2ESourceCoverage)
    answer_audit: E2EAnswerAudit = field(default_factory=E2EAnswerAudit)
    required_sources: tuple[str, ...] = ()
    missing_required_sources: tuple[str, ...] = ()
    privacy_guard_applied: bool = True
    warnings: tuple[str, ...] = ()
    next_action: str = ""

    @property
    def retrieval_ok(self) -> bool:
        return any(
            item.retrieval_succeeded
            and item.evidence_count > 0
            and not item.used_inventory_fallback
            for item in self.query_results
        )

    @property
    def answer_ok(self) -> bool:
        if self.mode in {"dry_run", "retrieval_only"}:
            return True
        return any(item.answer_succeeded for item in self.query_results)

    @property
    def ok(self) -> bool:
        if not self.db_exists:
            return False
        if self.mode == "dry_run":
            return True
        if self.missing_required_sources:
            return False
        return self.retrieval_ok and self.answer_ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "mode": self.mode,
            "db_exists": self.db_exists,
            "counts": self.counts.to_dict(),
            "indexes": self.indexes.to_dict(),
            "query_results": [item.to_dict() for item in self.query_results],
            "source_coverage": self.source_coverage.to_dict(),
            "answer_audit": self.answer_audit.to_dict(),
            "required_sources": list(self.required_sources),
            "missing_required_sources": list(self.missing_required_sources),
            "privacy_guard_applied": self.privacy_guard_applied,
            "warnings": list(self.warnings),
            "next_action": self.next_action,
            "privacy": {
                "output": (
                    "counts, source types, evidence ids, sanitized status, and "
                    "answer audit counters by default"
                ),
                "redacted": (
                    "query text, snippets unless explicitly requested, answer text unless "
                    "explicitly requested, filenames, paths, GPS, EXIF, OCR, raw messages, "
                    "note bodies, captions"
                ),
            },
        }


@dataclass(frozen=True)
class E2ESmokeOptions:
    """Options for the real-data E2E smoke workflow."""

    config_dir: Path | str | None = None
    paths_config: Path | str | None = None
    db_path: Path | str = DEFAULT_E2E_DB_PATH
    queries_config: Path | str | None = None
    queries: tuple[E2ESmokeQuery, ...] | None = None
    dry_run: bool = False
    retrieval_only: bool = False
    fake_model: bool = False
    real_model: bool = False
    no_fallback: bool = False
    diagnose: bool = False
    require_sources: tuple[str, ...] = ()
    query_limit: int | None = None
    query_id: str | None = None
    limit: int = DEFAULT_E2E_QUERY_LIMIT
    timeout_seconds: float | None = None
    max_tokens: int = DEFAULT_E2E_REAL_MODEL_MAX_TOKENS
    temperature: float = DEFAULT_E2E_REAL_MODEL_TEMPERATURE
    max_evidence_items: int = DEFAULT_E2E_MAX_EVIDENCE_ITEMS
    max_evidence_chars: int = DEFAULT_E2E_MAX_EVIDENCE_CHARS
    compact_evidence: bool = True
    json_retry: int = 1
    response_format_json: bool = False
    show_answer: bool = False
    show_snippets: bool = False
    show_model_output_metadata: bool = False
    show_model_output: bool = False
    semantic_model: str = "none"
    model_key: str = DEFAULT_E2E_LEADER_MODEL_KEY
    allow_remote: bool = False


@dataclass(frozen=True)
class _E2ELeaderRuntime:
    """Resolved leader endpoint metadata for one smoke run."""

    preflight: ChatEndpointPreflightResult
    base_url: str
    served_model_name: str
    timeout_seconds: float
    retries: int


def run_e2e_smoke(options: E2ESmokeOptions) -> E2ESmokeReport:
    """Run a privacy-safe E2E smoke check over existing local metadata."""

    config = load_config(config_dir=options.config_dir, paths_config=options.paths_config)
    mode = _resolve_mode(options)
    warnings: list[str] = []
    db_path = Path(options.db_path).expanduser()
    if not db_path.exists():
        return E2ESmokeReport(
            mode=mode,
            db_exists=False,
            warnings=("SQLite DB does not exist; run ingestion/indexing first if intended.",),
            next_action="Run pma stats with the intended --db path, then ingest or index data explicitly.",
        )

    counts, indexes = _inspect_database(db_path)
    required_sources = _parse_required_sources(options.require_sources)
    if counts.evidence_capable_source_count == 0:
        warnings.append("No evidence-capable sources were found in the local DB.")
    if not indexes.text_index_available and (counts.line_messages_count or counts.notes_count):
        warnings.append("LINE/notes exist but the text index is not available; run pma index text.")
    if (
        counts.media_annotations_count > 0
        and indexes.media_annotations_in_text_index_count < counts.media_annotations_count
    ):
        warnings.append(
            "photo annotation text index is behind latest annotations; run pma index text",
        )
    if indexes.embeddings_count and options.semantic_model == "none":
        warnings.append("embeddings exist but semantic retrieval is not enabled in this smoke path")
    if options.show_model_output:
        warnings.append(
            "raw model output preview requested; it may contain private evidence-derived content",
        )
    if options.show_answer:
        warnings.append(
            "answer display requested; do not paste local answer output into public chats",
        )
    if options.show_snippets:
        warnings.append(
            "snippet display requested; snippets may contain private evidence-derived content",
        )

    if mode == "dry_run":
        return E2ESmokeReport(
            mode=mode,
            db_exists=True,
            counts=counts,
            indexes=indexes,
            required_sources=required_sources,
            warnings=tuple(warnings),
            next_action=_next_action_for_report(mode, counts, indexes, False),
        )

    available_queries = options.queries or load_e2e_smoke_queries(
        config.config_dir,
        query_file=options.queries_config,
    )
    queries = _select_smoke_queries(
        available_queries,
        query_id=options.query_id,
        query_limit=options.query_limit,
    )
    leader_runtime: _E2ELeaderRuntime | None = None
    if mode == "real_model":
        try:
            leader_runtime = _preflight_real_model_leader(config, options)
        except (ModelRuntimeError, RuntimeError, ValueError) as exc:
            warnings.append("leader endpoint preflight failed: " + _safe_message(exc))
            return E2ESmokeReport(
                mode=mode,
                db_exists=True,
                counts=counts,
                indexes=indexes,
                required_sources=required_sources,
                warnings=tuple(warnings),
                next_action=(
                    "Start the configured local leader endpoint, run "
                    "pma models ping leader, then retry with --query-limit 1."
                ),
            )
        warnings.extend(leader_runtime.preflight.warnings)
    query_results = _run_query_checks(
        db_path,
        config=config,
        options=options,
        mode=mode,
        queries=queries,
        counts=counts,
        leader_runtime=leader_runtime,
    )
    if not any(result.evidence_count > 0 for result in query_results):
        warnings.append("No configured smoke query returned evidence.")

    source_coverage = _source_coverage_from_results(query_results)
    answer_audit = _answer_audit_from_results(query_results)
    missing_required_sources = _missing_required_sources(required_sources, source_coverage)
    if missing_required_sources:
        warnings.append(
            "required sources were available but returned no evidence: "
            + ",".join(missing_required_sources),
        )
    if counts.notes_count and source_coverage.real_note_evidence_count == 0:
        warnings.append("notes source was available but no note evidence was returned")
    retrieval_ok = any(
        result.retrieval_succeeded
        and result.evidence_count > 0
        and not result.used_inventory_fallback
        for result in query_results
    )
    return E2ESmokeReport(
        mode=mode,
        db_exists=True,
        counts=counts,
        indexes=indexes,
        query_results=tuple(query_results),
        source_coverage=source_coverage,
        answer_audit=answer_audit,
        required_sources=required_sources,
        missing_required_sources=missing_required_sources,
        warnings=tuple(warnings),
        next_action=_next_action_for_report(mode, counts, indexes, retrieval_ok),
    )


def load_e2e_smoke_queries(
    config_dir: Path | str,
    *,
    query_file: Path | str | None = None,
) -> tuple[E2ESmokeQuery, ...]:
    """Load safe smoke queries from local override, example config, or defaults."""

    config_root = Path(config_dir).expanduser()
    if query_file is not None:
        path = Path(query_file).expanduser()
    else:
        local_path = config_root / LOCAL_E2E_QUERY_FILENAME
        path = local_path if local_path.exists() else config_root / DEFAULT_E2E_QUERY_FILENAME
    if not path.exists():
        return tuple(
            E2ESmokeQuery(query_id=query_id, text=text, sources=sources)
            for query_id, text, sources in _DEFAULT_SMOKE_QUERIES
        )

    try:
        raw = _load_query_profile(path)
    except (OSError, ConfigError) as exc:
        raise ValueError("unable to load E2E smoke query config") from exc
    queries = raw.get("queries")
    if not isinstance(queries, dict):
        raise ValueError("E2E smoke query config must contain a queries mapping")
    loaded: list[E2ESmokeQuery] = []
    for query_id, raw_query in queries.items():
        if not isinstance(raw_query, dict):
            continue
        text = str(raw_query.get("text") or "").strip()
        if not text:
            continue
        sources = _parse_sources(raw_query.get("sources"))
        loaded.append(E2ESmokeQuery(query_id=str(query_id), text=text, sources=sources))
    if not loaded:
        raise ValueError("E2E smoke query config did not contain any usable queries")
    return tuple(loaded)


def _select_smoke_queries(
    queries: tuple[E2ESmokeQuery, ...],
    *,
    query_id: str | None,
    query_limit: int | None,
) -> tuple[E2ESmokeQuery, ...]:
    selected = queries
    if query_id is not None:
        normalized_query_id = query_id.strip()
        selected = tuple(query for query in selected if query.query_id == normalized_query_id)
        if not selected:
            match = re.fullmatch(r"query_(\d+)", normalized_query_id)
            if match is not None:
                index = int(match.group(1))
                if 1 <= index <= len(queries):
                    selected = (queries[index - 1],)
        if not selected:
            raise ValueError("configured E2E smoke query id was not found")
    if query_limit is not None:
        if query_limit <= 0:
            raise ValueError("query_limit must be positive")
        selected = selected[:query_limit]
    if not selected:
        raise ValueError("no E2E smoke queries selected")
    return selected


def _load_query_profile(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if any(line.strip().startswith("- ") for line in text.splitlines()):
        parsed = _parse_query_list_yaml(text)
        if parsed is not None:
            return parsed
    try:
        return _parse_simple_yaml(text)
    except ConfigError:
        parsed = _parse_query_list_yaml(text)
        if parsed is not None:
            return parsed
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ConfigError("unsupported query YAML shape") from exc
        loaded = yaml.safe_load(text)
        if not isinstance(loaded, dict):
            raise ConfigError("query YAML must be a mapping")
        return loaded


def _parse_query_list_yaml(text: str) -> dict[str, Any] | None:
    """Parse the small list-style query profile shape without requiring PyYAML."""

    in_queries = False
    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    pending_list_key: str | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "queries:":
            in_queries = True
            continue
        if not in_queries:
            continue
        if stripped.startswith("- "):
            value = stripped[2:].strip()
            if pending_list_key and current is not None and ":" not in value:
                current.setdefault(pending_list_key, []).append(_query_yaml_scalar(value))
                continue
            current = {}
            items.append(current)
            pending_list_key = None
            if value:
                key, separator, raw_value = value.partition(":")
                if separator == ":":
                    current[key.strip()] = _query_yaml_scalar(raw_value.strip())
            continue
        if current is None or ":" not in stripped:
            continue
        key, _, raw_value = stripped.partition(":")
        clean_key = key.strip()
        clean_value = raw_value.strip()
        if clean_value:
            current[clean_key] = _query_yaml_scalar(clean_value)
            pending_list_key = None
        else:
            current[clean_key] = []
            pending_list_key = clean_key
    if not items:
        return None
    queries: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items, start=1):
        query_id = str(item.get("id") or item.get("query_id") or f"query_{index}")
        queries[query_id] = item
    return {"queries": queries}


def _query_yaml_scalar(value: str) -> Any:
    normalized = value.strip()
    if not normalized:
        return ""
    if normalized in {"[]", "{}"}:
        return [] if normalized == "[]" else {}
    if normalized.startswith("[") and normalized.endswith("]"):
        inner = normalized[1:-1].strip()
        if not inner:
            return []
        return [_query_yaml_scalar(part.strip()) for part in inner.split(",")]
    if (
        len(normalized) >= 2
        and normalized[0] == normalized[-1]
        and normalized[0] in {"'", '"'}
    ):
        return normalized[1:-1]
    return normalized


def format_e2e_smoke_report(report: E2ESmokeReport) -> str:
    """Return a human-readable report without private content."""

    status = "passed" if report.ok else "needs-attention"
    counts = report.counts
    indexes = report.indexes
    lines = [
        f"E2E smoke {status}: mode={report.mode}; db_exists={str(report.db_exists).lower()}",
        (
            "counts: "
            f"media_items={counts.media_items_count}; "
            f"media_annotations={counts.media_annotations_count}; "
            f"line_messages={counts.line_messages_count}; "
            f"notes={counts.notes_count}; "
            f"evidence_capable_sources={counts.evidence_capable_source_count}"
        ),
        "available_sources=" + (",".join(counts.available_sources) if counts.available_sources else "none"),
        (
            "indexes: "
            f"text_documents={indexes.text_documents_count}; "
            f"text_index_available={str(indexes.text_index_available).lower()}; "
            f"text_fts_available={str(indexes.text_fts_available).lower()}; "
            f"embeddings={indexes.embeddings_count}; "
            f"embeddings_count_kind={indexes.embeddings_count_kind}; "
            f"vector_index_status={indexes.vector_index_status}"
        ),
        (
            "index_sources: "
            f"text_documents_table={indexes.text_documents_table or 'none'}; "
            f"text_documents_count_kind={indexes.text_documents_count_kind}; "
            f"text_documents_derived_from={','.join(indexes.text_documents_derived_from) or 'none'}; "
            f"media_annotations_searchable={indexes.media_annotations_searchable}; "
            f"media_annotations_searchable_via={','.join(indexes.media_annotations_searchable_via) or 'none'}"
        ),
    ]
    coverage = report.source_coverage
    lines.append(
        "source_coverage: "
        f"real_photo={coverage.real_photo_evidence_count}; "
        f"real_line={coverage.real_line_evidence_count}; "
        f"real_note={coverage.real_note_evidence_count}; "
        f"fallback={coverage.fallback_evidence_count}; "
        f"zero_queries={coverage.queries_with_zero_evidence}; "
        f"fallback_only_queries={coverage.queries_with_only_fallback_evidence}; "
        f"mixed_source_queries={coverage.queries_with_mixed_sources}"
    )
    audit = report.answer_audit
    lines.append(
        "answer_audit: "
        f"succeeded={audit.answer_succeeded_count}; "
        f"validation_errors={audit.answer_validation_error_count}; "
        f"retry_used={audit.retry_used_count}; "
        f"retry_success={audit.retry_success_count}; "
        f"average_confidence={audit.average_confidence}; "
        f"evidence_reference_coverage={audit.evidence_reference_coverage}; "
        f"unknown_evidence_refs={audit.unknown_evidence_reference_count}; "
        f"empty_used_sources={audit.queries_with_empty_used_sources}; "
        f"empty_unknowns={audit.queries_with_empty_unknowns}; "
        f"answer_sources={_format_counts(audit.answer_source_counts)}"
    )
    if report.query_results:
        lines.append("queries:")
        for item in report.query_results:
            answer = "skipped"
            if item.answer_succeeded:
                answer = f"ok confidence={item.answer_confidence}"
            elif item.error_class:
                answer = f"error class={item.error_class}"
            fallback = "; inventory_fallback=true" if item.used_inventory_fallback else ""
            fallback_reason = (
                f"; fallback_reason={item.fallback_reason}" if item.fallback_reason else ""
            )
            lines.append(
                "  "
                f"{item.query_label}: "
                f"sources={','.join(item.sources) or 'all'}; "
                f"retrieval={'ok' if item.retrieval_succeeded else 'empty'}; "
                f"evidence_items={item.evidence_count}; "
                f"evidence_ids={','.join(item.evidence_ids) if item.evidence_ids else 'none'}; "
                f"answer={answer}"
                f"{fallback}"
                f"{fallback_reason}",
            )
            if item.model_id or item.prompt_chars is not None:
                lines.append(
                    "    model: "
                    f"id={item.model_id or 'none'}; "
                    f"endpoint={item.endpoint_url or 'none'}; "
                    f"timeout_seconds={item.timeout_seconds}; "
                    f"max_tokens={item.max_tokens}; "
                    f"prompt_chars={item.prompt_chars}; "
                    f"evidence_sent={item.evidence_sent_count}"
                )
            if item.raw_response_chars is not None or item.json_extraction_strategy:
                lines.append(
                    "    answer_json: "
                    f"raw_response_chars={item.raw_response_chars}; "
                    f"json_extraction_succeeded={item.json_extraction_succeeded}; "
                    f"json_extraction_strategy={item.json_extraction_strategy or 'none'}; "
                    f"retry_used={item.json_retry_used}; "
                    f"retry_succeeded={item.json_retry_succeeded}; "
                    f"contains_json_like_object={item.contains_json_like_object}; "
                    f"contains_think_tag={item.contains_think_tag}; "
                    f"contains_fenced_json={item.contains_fenced_json}; "
                    f"extraction_attempts={item.extraction_attempts}; "
                    f"allowed_evidence_count={item.allowed_evidence_count}; "
                    f"allowed_sources={','.join(item.allowed_sources) or 'none'}; "
                    f"validation_error={item.answer_validation_error_class or 'none'}"
                )
            if item.raw_model_output_preview:
                lines.append(
                    "    raw_model_output_preview: "
                    + item.raw_model_output_preview
                )
            if item.answer_conclusion is not None:
                lines.append("    answer:")
                lines.append(f"      conclusion: {item.answer_conclusion}")
                lines.append(
                    "      evidence_references: "
                    + (",".join(item.answer_evidence_references) or "none")
                )
                lines.append(
                    "      used_sources: "
                    + (",".join(item.used_sources) or "none")
                )
                lines.append(
                    "      unknowns: "
                    + (" | ".join(item.answer_unknowns) if item.answer_unknowns else "none")
                )
            if item.safe_snippets:
                lines.append("    snippets:")
                for snippet in item.safe_snippets:
                    lines.append(
                        "      "
                        f"id={snippet.get('evidence_id', 'unknown')}; "
                        f"source={snippet.get('source', 'unknown')}; "
                        f"snippet={snippet.get('snippet', '')}"
                    )
            if item.retrieval_stage_counts:
                stage = item.retrieval_stage_counts
                lines.append(
                    "    stages: "
                    f"fts={stage.get('fts_candidate_count', 0)}; "
                    f"exact_like={stage.get('exact_like_candidate_count', 0)}; "
                    f"keyword_like={stage.get('keyword_like_candidate_count', 0)}; "
                    f"text={stage.get('text_candidate_count', 0)}; "
                    f"semantic={stage.get('semantic_candidate_count', 0)}; "
                    f"media_annotations={stage.get('media_annotation_candidate_count', 0)}; "
                    f"after_source_filter={stage.get('candidate_count_after_source_filter', 0)}; "
                    f"final={stage.get('final_evidence_count', 0)}"
                )
            if item.source_stage_counts:
                for source in ("photos", "line", "notes"):
                    source_stage = item.source_stage_counts.get(source)
                    if not source_stage:
                        continue
                    lines.append(
                        f"    {source}: "
                        f"text={source_stage.get('text_candidate_count', 0)}; "
                        f"fts={source_stage.get('fts_candidate_count', 0)}; "
                        f"exact_like={source_stage.get('exact_like_candidate_count', 0)}; "
                        f"keyword_like={source_stage.get('keyword_like_candidate_count', 0)}; "
                        f"after_filter={source_stage.get('candidate_count_after_source_filter', 0)}; "
                        f"final={source_stage.get('candidate_count_after_ranking', 0)}; "
                        f"reason={source_stage.get('drop_reason') or 'none'}"
                    )
    if report.warnings:
        lines.append("warnings:")
        lines.extend(f"  - {warning}" for warning in report.warnings)
    lines.append(
        "privacy: query text, snippets, filenames, full paths, GPS, EXIF, OCR, "
        "raw messages, note bodies, and captions are not printed."
    )
    if report.next_action:
        lines.append(f"next_action: {report.next_action}")
    return "\n".join(lines)


def _resolve_mode(options: E2ESmokeOptions) -> str:
    if options.dry_run:
        return "dry_run"
    if options.retrieval_only:
        return "retrieval_only"
    if options.real_model:
        return "real_model"
    return "fake_model"


def _inspect_database(db_path: Path) -> tuple[E2ESmokeCounts, E2EIndexStatus]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        media_items_count = _count_table(connection, "media_items", "is_excluded = 0")
        media_annotations_count = _count_table(
            connection,
            "media_annotations",
            "is_excluded = 0 AND annotation_type = 'vision'",
        )
        line_messages_count = _count_table(connection, "line_messages", "is_excluded = 0")
        notes_count = _count_table(connection, "notes", "is_excluded = 0")
        source_items_count = _count_table(connection, "source_items", "is_excluded = 0")
        available_sources = _available_sources(
            media_annotations_count=media_annotations_count,
            line_messages_count=line_messages_count,
            notes_count=notes_count,
        )
        counts = E2ESmokeCounts(
            media_items_count=media_items_count,
            media_annotations_count=media_annotations_count,
            line_messages_count=line_messages_count,
            notes_count=notes_count,
            source_items_count=source_items_count,
            evidence_capable_source_count=len(available_sources),
            available_sources=available_sources,
        )
        source_coverage = inspect_source_coverage(db_path)
        text = source_coverage.text
        embeddings = source_coverage.embeddings
        media = source_coverage.media_annotations
        indexes = E2EIndexStatus(
            text_documents_count=text.text_documents_count,
            text_index_available=text.text_documents_count > 0,
            text_fts_available=text.text_search_fts_table_exists,
            text_documents_count_kind=text.text_documents_count_kind,
            text_documents_table=text.text_documents_table,
            text_documents_derived_from=text.text_documents_derived_from,
            text_documents_source_breakdown=text.text_documents_source_breakdown,
            embeddings_count=embeddings.embeddings_count,
            embedding_index_available=embeddings.embeddings_count > 0,
            embeddings_count_kind=embeddings.embeddings_count_kind,
            embeddings_derived_from=embeddings.embeddings_derived_from,
            embedding_source_breakdown_available=embeddings.embedding_source_breakdown_available,
            embedding_source_breakdown=embeddings.embedding_source_breakdown,
            vector_index_status="available" if embeddings.embeddings_count > 0 else "not_available",
            media_annotations_in_text_index_count=media.media_annotations_in_text_index_count,
            media_annotations_searchable=media.media_annotations_searchable,
            media_annotations_searchable_via=media.media_annotations_searchable_via,
            photo_evidence_retrievable=media.photo_evidence_retrievable,
        )
        return counts, indexes
    finally:
        connection.close()


def _run_query_checks(
    db_path: Path,
    *,
    config: ConfigBundle,
    options: E2ESmokeOptions,
    mode: str,
    queries: tuple[E2ESmokeQuery, ...],
    counts: E2ESmokeCounts,
    leader_runtime: _E2ELeaderRuntime | None = None,
) -> list[E2ESmokeQueryResult]:
    service = RetrievalService(db_path, ensure_index=False)
    results: list[E2ESmokeQueryResult] = []
    for index, query in enumerate(queries, start=1):
        results.append(
            _run_one_query_check(
                service,
                query,
                query_label=f"query_{index}",
                mode=mode,
                config=config,
                options=options,
                leader_runtime=leader_runtime,
            ),
        )

    if (
        not any(result.evidence_count > 0 for result in results)
        and counts.media_annotations_count > 0
        and not options.no_fallback
    ):
        fallback = E2ESmokeQuery(
            query_id="inventory_fallback",
            text="",
            sources=("photos",),
        )
        results.append(
            _run_one_query_check(
                service,
                fallback,
                query_label="inventory_fallback",
                mode=mode,
                config=config,
                options=options,
                leader_runtime=leader_runtime,
                used_inventory_fallback=True,
                fallback_reason="configured smoke queries returned no real evidence",
            ),
        )
    return results


def _run_one_query_check(
    service: RetrievalService,
    query: E2ESmokeQuery,
    *,
    query_label: str,
    mode: str,
    config: ConfigBundle,
    options: E2ESmokeOptions,
    leader_runtime: _E2ELeaderRuntime | None = None,
    used_inventory_fallback: bool = False,
    fallback_reason: str | None = None,
) -> E2ESmokeQueryResult:
    try:
        retrieval = service.retrieve(
            query.text,
            filters=RetrievalFilters(sources=query.sources),
            limit=max(1, options.limit),
            redact_for_display=True,
        )
    except (RuntimeError, ValueError) as exc:
        return E2ESmokeQueryResult(
            query_label=query_label,
            sources=query.sources,
            retrieval_succeeded=False,
            evidence_count=0,
            used_inventory_fallback=used_inventory_fallback,
            fallback_reason=fallback_reason,
            error_class=exc.__class__.__name__,
            error_message=_safe_message(exc),
        )

    raw_evidence = retrieval.evidence
    evidence = _privacy_safe_evidence(raw_evidence)
    evidence_ids = tuple(item.evidence_id for item in evidence[: options.limit])
    source_counts = _evidence_source_counts(evidence)
    stage_counts: dict[str, int] = {}
    source_stage_counts: dict[str, dict[str, Any]] = {}
    if options.diagnose or options.no_fallback or not evidence:
        stage_counts, source_stage_counts = _safe_retrieval_stage_counts(
            service.db_path,
            query_label=query_label,
            query=query,
            limit=max(1, options.limit),
        )
    base = {
        "query_label": query_label,
        "sources": query.sources,
        "retrieval_succeeded": bool(evidence),
        "evidence_count": len(evidence),
        "evidence_ids": evidence_ids,
        "used_inventory_fallback": used_inventory_fallback,
        "evidence_source_counts": source_counts,
        "retrieval_stage_counts": stage_counts,
        "source_stage_counts": source_stage_counts,
        "fallback_reason": fallback_reason,
        "safe_snippets": _safe_snippet_display(raw_evidence[: options.limit])
        if options.show_snippets
        else (),
    }
    if mode == "retrieval_only":
        return E2ESmokeQueryResult(**base)

    model_evidence, packed_evidence = _model_evidence_packet(evidence, options)
    prompt_chars = len(
        build_leader_prompt(
            query.text,
            packed_evidence,
            allowed_evidence_ids=tuple(item.evidence_id for item in model_evidence),
            allowed_sources=tuple(_ordered_evidence_sources(model_evidence)),
        ),
    )
    model_metadata = _model_query_metadata(
        mode=mode,
        leader_runtime=leader_runtime,
        options=options,
        prompt_chars=prompt_chars,
        evidence_sent_count=len(model_evidence),
    )
    try:
        leader = _build_smoke_leader_agent(config, options, mode, leader_runtime)
        leader_result = leader.answer_with_diagnostics(
            question=query.text,
            retrieval_result=RetrievalResult(
                question=query.text,
                evidence=model_evidence,
                packed_evidence=packed_evidence,
                redacted=True,
            ),
        )
        answer = leader_result.answer
    except (AnswerValidationError, ModelRuntimeError, RuntimeError, ValueError) as exc:
        diagnostics = diagnostics_from_error(exc)
        return E2ESmokeQueryResult(
            **base,
            **model_metadata,
            **_answer_diagnostics_metadata(diagnostics),
            error_class=exc.__class__.__name__,
            error_message=_safe_message(exc),
        )
    return E2ESmokeQueryResult(
        **base,
        **model_metadata,
        **_answer_diagnostics_metadata(leader_result.diagnostics),
        answer_succeeded=True,
        answer_confidence=answer.confidence,
        answer_evidence_references=answer.evidence_references,
        used_sources=answer.used_sources,
        answer_conclusion=answer.conclusion if options.show_answer else None,
        answer_unknowns=answer.unknowns if options.show_answer else (),
        answer_unknown_count=len(answer.unknowns),
        answer_evidence_reference_count=len(answer.evidence_references),
        answer_used_source_count=len(answer.used_sources),
    )


def _source_coverage_from_results(
    results: list[E2ESmokeQueryResult],
) -> E2ESourceCoverage:
    real_counts = {"photos": 0, "line": 0, "notes": 0}
    fallback_count = 0
    zero_queries = 0
    fallback_only = 0
    mixed = 0
    for result in results:
        if result.evidence_count == 0:
            zero_queries += 1
            continue
        if result.used_inventory_fallback:
            fallback_count += result.evidence_count
            fallback_only += 1
            continue
        active_sources = {
            source for source, count in result.evidence_source_counts.items() if count > 0
        }
        if len(active_sources) > 1:
            mixed += 1
        for source in real_counts:
            real_counts[source] += int(result.evidence_source_counts.get(source, 0))
    return E2ESourceCoverage(
        real_photo_evidence_count=real_counts["photos"],
        real_line_evidence_count=real_counts["line"],
        real_note_evidence_count=real_counts["notes"],
        fallback_evidence_count=fallback_count,
        queries_with_zero_evidence=zero_queries,
        queries_with_only_fallback_evidence=fallback_only,
        queries_with_mixed_sources=mixed,
    )


def _answer_audit_from_results(
    results: list[E2ESmokeQueryResult],
) -> E2EAnswerAudit:
    confidence_values: list[float] = []
    total_reference_count = 0
    total_allowed_evidence = 0
    answer_source_counts: dict[str, int] = {}
    answer_succeeded_count = 0
    answer_validation_error_count = 0
    retry_used_count = 0
    retry_success_count = 0
    unknown_evidence_reference_count = 0
    empty_used_sources = 0
    empty_unknowns = 0

    for result in results:
        if result.answer_succeeded:
            answer_succeeded_count += 1
            if result.answer_confidence is not None:
                confidence_values.append(result.answer_confidence)
            reference_count = (
                result.answer_evidence_reference_count
                if result.answer_evidence_reference_count is not None
                else len(result.answer_evidence_references)
            )
            total_reference_count += reference_count
            total_allowed_evidence += int(
                result.evidence_sent_count or result.allowed_evidence_count or 0,
            )
            for source in result.used_sources:
                answer_source_counts[source] = answer_source_counts.get(source, 0) + 1
            if (result.answer_used_source_count or 0) == 0:
                empty_used_sources += 1
            if (result.answer_unknown_count or 0) == 0:
                empty_unknowns += 1
        if result.answer_validation_error_class == "AnswerValidationError" or (
            result.error_class == "AnswerValidationError"
        ):
            answer_validation_error_count += 1
        if result.json_retry_used:
            retry_used_count += 1
        if result.json_retry_succeeded:
            retry_success_count += 1
        if result.error_message and "unknown_evidence_reference" in result.error_message:
            unknown_evidence_reference_count += 1

    average_confidence = None
    if confidence_values:
        average_confidence = round(sum(confidence_values) / len(confidence_values), 4)
    evidence_reference_coverage = None
    if total_allowed_evidence > 0:
        evidence_reference_coverage = round(
            min(1.0, total_reference_count / total_allowed_evidence),
            4,
        )

    return E2EAnswerAudit(
        answer_succeeded_count=answer_succeeded_count,
        answer_validation_error_count=answer_validation_error_count,
        retry_used_count=retry_used_count,
        retry_success_count=retry_success_count,
        average_confidence=average_confidence,
        evidence_reference_coverage=evidence_reference_coverage,
        unknown_evidence_reference_count=unknown_evidence_reference_count,
        answer_source_counts=answer_source_counts,
        queries_with_empty_used_sources=empty_used_sources,
        queries_with_empty_unknowns=empty_unknowns,
    )


def _evidence_source_counts(evidence: tuple[Evidence, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in evidence:
        counts[item.source_kind] = counts.get(item.source_kind, 0) + 1
    return counts


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ",".join(f"{key}:{counts[key]}" for key in sorted(counts))


def _model_evidence_packet(
    evidence: tuple[Evidence, ...],
    options: E2ESmokeOptions,
) -> tuple[tuple[Evidence, ...], str]:
    max_items = options.max_evidence_items
    max_chars = options.max_evidence_chars
    if max_items <= 0:
        raise ValueError("max_evidence_items must be positive")
    if max_chars <= 0:
        raise ValueError("max_evidence_chars must be positive")

    selected = evidence[:max_items]
    packer = _pack_compact_evidence if options.compact_evidence else _pack_redacted_evidence
    packed = packer(selected)
    while len(packed) > max_chars and len(selected) > 1:
        selected = selected[:-1]
        packed = packer(selected)
    if len(packed) > max_chars:
        packed = _truncate_evidence_packet(packed, max_chars)
    return selected, packed


def _pack_redacted_evidence(evidence: tuple[Evidence, ...]) -> str:
    return pack_evidence_for_prompt(evidence, redact_private=True)


def _pack_compact_evidence(evidence: tuple[Evidence, ...]) -> str:
    if not evidence:
        return "No local evidence retrieved."
    lines = ["Local evidence (compact, private text redacted):"]
    for index, item in enumerate(evidence, start=1):
        lines.append(
            " ".join(
                [
                    f"[{index}]",
                    f"id={item.evidence_id}",
                    f"source={item.source_kind}",
                    f"table={item.source_table}",
                    f"source_id={item.source_id}",
                    f"confidence={item.confidence:.3f}",
                    f"score={item.score:.3f}",
                    f"signals={','.join(item.signals)}",
                ],
            ),
        )
        if item.occurred_at:
            lines.append(f"date: {item.occurred_at}")
    return "\n".join(lines)


def _truncate_evidence_packet(text: str, max_chars: int) -> str:
    marker = "\n[evidence packet truncated by configured character budget]"
    if max_chars <= len(marker):
        return marker[-max_chars:]
    return text[: max_chars - len(marker)].rstrip() + marker


def _model_query_metadata(
    *,
    mode: str,
    leader_runtime: _E2ELeaderRuntime | None,
    options: E2ESmokeOptions,
    prompt_chars: int,
    evidence_sent_count: int,
) -> dict[str, Any]:
    if mode != "real_model" or leader_runtime is None:
        return {}
    return {
        "model_id": leader_runtime.served_model_name,
        "endpoint_url": leader_runtime.base_url,
        "timeout_seconds": leader_runtime.timeout_seconds,
        "max_tokens": options.max_tokens,
        "prompt_chars": prompt_chars,
        "evidence_sent_count": evidence_sent_count,
    }


def _answer_diagnostics_metadata(diagnostics: Any | None) -> dict[str, Any]:
    if diagnostics is None:
        return {}
    return {
        "raw_response_chars": diagnostics.raw_response_chars,
        "json_extraction_succeeded": diagnostics.json_extraction_succeeded,
        "json_extraction_strategy": diagnostics.json_extraction_strategy,
        "answer_validation_error_class": diagnostics.answer_validation_error_class,
        "answer_validation_error_message": diagnostics.answer_validation_error_message,
        "contains_json_like_object": diagnostics.contains_json_like_object,
        "contains_think_tag": diagnostics.contains_think_tag,
        "contains_fenced_json": diagnostics.contains_fenced_json,
        "extraction_attempts": diagnostics.extraction_attempts,
        "json_retry_used": diagnostics.json_retry_used,
        "json_retry_succeeded": diagnostics.json_retry_succeeded,
        "allowed_evidence_count": diagnostics.allowed_evidence_count,
        "allowed_sources": diagnostics.allowed_sources,
        "raw_model_output_preview": diagnostics.raw_model_output_preview,
    }


def _ordered_evidence_sources(evidence: tuple[Evidence, ...]) -> tuple[str, ...]:
    sources: list[str] = []
    for item in evidence:
        if item.source_kind not in sources:
            sources.append(item.source_kind)
    return tuple(sources)


def _safe_retrieval_stage_counts(
    db_path: Path,
    *,
    query_label: str,
    query: E2ESmokeQuery,
    limit: int,
) -> tuple[dict[str, int], dict[str, dict[str, Any]]]:
    try:
        diagnostics = diagnose_retrieval_query(
            db_path,
            query_label=query_label,
            query=query.text,
            sources=query.sources,
            limit=limit,
        )
    except (RuntimeError, ValueError, sqlite3.DatabaseError):
        return {}, {}
    return (
        {
            "fts_candidate_count": diagnostics.fts_candidate_count,
            "exact_like_candidate_count": diagnostics.exact_like_candidate_count,
            "keyword_like_candidate_count": diagnostics.keyword_like_candidate_count,
            "text_candidate_count": diagnostics.text_candidate_count,
            "semantic_candidate_count": diagnostics.semantic_candidate_count,
            "media_annotation_candidate_count": diagnostics.media_annotation_candidate_count,
            "candidate_count_after_source_filter": diagnostics.candidate_count_after_source_filter,
            "candidate_count_after_ranking": diagnostics.candidate_count_after_ranking,
            "final_evidence_count": diagnostics.final_evidence_count,
        },
        diagnostics.source_stage_counts,
    )


def _build_smoke_leader_agent(
    config: ConfigBundle,
    options: E2ESmokeOptions,
    mode: str,
    leader_runtime: _E2ELeaderRuntime | None = None,
) -> LeaderAgent:
    if mode != "real_model":
        return LeaderAgent(FakeLeaderChatModelClient(), model_id="fake-leader")
    if leader_runtime is None:
        leader_runtime = _preflight_real_model_leader(config, options)
    client = OpenAICompatibleHTTPClient(
        base_url=leader_runtime.base_url,
        model=leader_runtime.served_model_name,
        timeout_seconds=leader_runtime.timeout_seconds,
        retries=leader_runtime.retries,
        allow_remote=options.allow_remote,
    )
    return LeaderAgent(
        client,
        model_id=leader_runtime.served_model_name,
        max_tokens=options.max_tokens,
        temperature=options.temperature,
        json_response_format=options.response_format_json,
        json_retry=options.json_retry,
        show_model_output=options.show_model_output,
    )


def _preflight_real_model_leader(
    config: ConfigBundle,
    options: E2ESmokeOptions,
) -> _E2ELeaderRuntime:
    model_spec = config.model_registry.get(options.model_key)
    if model_spec is None:
        raise ValueError("configured leader model key was not found")
    endpoint = endpoint_from_model_spec(model_spec)
    if endpoint is None:
        raise ValueError("configured leader model endpoint_url is missing")
    preflight = preflight_chat_endpoint(
        endpoint,
        allow_remote=options.allow_remote,
    )
    timeout_seconds = (
        options.timeout_seconds
        if options.timeout_seconds is not None
        else endpoint.request_timeout_seconds or DEFAULT_E2E_REAL_MODEL_TIMEOUT_SECONDS
    )
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if options.max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    return _E2ELeaderRuntime(
        preflight=preflight,
        base_url=endpoint.base_url,
        served_model_name=preflight.served_model_name,
        timeout_seconds=timeout_seconds,
        retries=endpoint.retries,
    )


def _privacy_safe_evidence(evidence: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
    guard = PrivacyGuard()
    return guard.redact_evidence(evidence, redact_private=True)


def _safe_snippet_display(evidence: tuple[Evidence, ...]) -> tuple[dict[str, str], ...]:
    """Return explicit, truncated local-only snippets with no paths or metadata."""

    guard = PrivacyGuard()
    snippets: list[dict[str, str]] = []
    for item in evidence:
        if not item.snippet:
            continue
        snippets.append(
            {
                "evidence_id": item.evidence_id,
                "source": item.source_kind,
                "snippet": _truncate_display_text(guard.redact_text(item.snippet), 160),
            },
        )
    return tuple(snippets)


def _truncate_display_text(text: str, max_chars: int) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    normalized = _PATH_LIKE_RE.sub("[path redacted]", normalized)
    normalized = _PRECISE_DECIMAL_RE.sub("[coordinate redacted]", normalized)
    if len(normalized) <= max_chars:
        return normalized
    if max_chars <= 3:
        return normalized[:max_chars]
    return normalized[: max_chars - 3].rstrip() + "..."


def _available_sources(
    *,
    media_annotations_count: int,
    line_messages_count: int,
    notes_count: int,
) -> tuple[str, ...]:
    sources: list[str] = []
    if media_annotations_count > 0:
        sources.append("photos")
    if line_messages_count > 0:
        sources.append("line")
    if notes_count > 0:
        sources.append("notes")
    return tuple(sources)


def _count_table(connection: sqlite3.Connection, table_name: str, where: str | None = None) -> int:
    if not _table_exists(connection, table_name):
        return 0
    sql = f"SELECT COUNT(*) AS count FROM {table_name}"
    if where:
        sql += f" WHERE {where}"
    row = connection.execute(sql).fetchone()
    return int(row["count"] if row is not None else 0)


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


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
    unknown = set(sources) - SUPPORTED_E2E_SOURCES
    if unknown:
        raise ValueError(f"unsupported E2E smoke sources: {sorted(unknown)}")
    return sources


def _parse_required_sources(values: tuple[str, ...]) -> tuple[str, ...]:
    required = tuple(dict.fromkeys(source.strip().lower() for source in values if source.strip()))
    unknown = set(required) - SUPPORTED_E2E_SOURCES
    if unknown:
        raise ValueError(f"unsupported required E2E smoke sources: {sorted(unknown)}")
    return required


def _missing_required_sources(
    required_sources: tuple[str, ...],
    coverage: E2ESourceCoverage,
) -> tuple[str, ...]:
    counts = {
        "photos": coverage.real_photo_evidence_count,
        "line": coverage.real_line_evidence_count,
        "notes": coverage.real_note_evidence_count,
    }
    return tuple(source for source in required_sources if counts.get(source, 0) == 0)


def _safe_message(exc: BaseException) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    if "/" in message or "\\" in message:
        return exc.__class__.__name__
    return re.sub(r"\s+", " ", message)[:160]


def _next_action_for_report(
    mode: str,
    counts: E2ESmokeCounts,
    indexes: E2EIndexStatus,
    retrieval_ok: bool,
) -> str:
    if counts.evidence_capable_source_count == 0:
        return "Ingest or annotate local data explicitly, then rerun the smoke command."
    if (counts.line_messages_count or counts.notes_count) and not indexes.text_index_available:
        return "Run pma index text, then rerun pma e2e smoke --retrieval-only."
    if mode == "dry_run":
        return "Run pma e2e smoke --retrieval-only to check evidence retrieval."
    if not retrieval_ok:
        return "Adjust safe smoke queries or build indexes; no private content was printed."
    if mode == "retrieval_only":
        return "Run pma e2e smoke --fake-model for structured answer validation."
    return "Use pma query for manual quality checks when ready."


def report_to_json(report: E2ESmokeReport) -> str:
    """Serialize a smoke report as deterministic JSON."""

    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
