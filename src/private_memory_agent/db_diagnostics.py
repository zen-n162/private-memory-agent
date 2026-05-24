"""Privacy-safe SQLite schema and retrieval diagnostics."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from private_memory_agent.retrieval import (
    RetrievalFilters,
    RetrievalService,
    diagnose_text_search,
    extract_query_terms,
    media_annotation_search_text,
    search_text,
)
from private_memory_agent.retrieval.text import normalize_text

KNOWN_ROW_COUNT_TABLES = (
    "source_items",
    "media_items",
    "media_annotations",
    "line_messages",
    "notes",
    "entities",
    "events",
    "evidence_links",
    "embeddings",
    "audit_log",
    "text_search_documents",
    "text_search_fts",
    "text_annotations",
)
EMBEDDING_OWNER_SOURCE_MAP = {
    "line_messages": "line",
    "notes": "notes",
    "media_items": "photos",
    "media_annotations": "photos",
}
MEDIA_ANNOTATION_AUDIT_SCAN_LIMIT = 5000


@dataclass(frozen=True)
class SchemaTableInfo:
    """Schema metadata for one SQLite table."""

    name: str
    columns: tuple[str, ...]
    indexes: tuple[str, ...] = ()
    row_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "columns": list(self.columns),
            "indexes": list(self.indexes),
            "row_count": self.row_count,
        }


@dataclass(frozen=True)
class SchemaViewInfo:
    """Schema metadata for one SQLite view."""

    name: str
    columns: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "columns": list(self.columns)}


@dataclass(frozen=True)
class SchemaIndexInfo:
    """Schema metadata for one SQLite index."""

    name: str
    table_name: str | None
    columns: tuple[str, ...] = ()
    unique: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "table_name": self.table_name,
            "columns": list(self.columns),
            "unique": self.unique,
        }


@dataclass(frozen=True)
class DatabaseSchemaReport:
    """Privacy-safe database schema report."""

    db_exists: bool
    tables: tuple[SchemaTableInfo, ...] = ()
    views: tuple[SchemaViewInfo, ...] = ()
    indexes: tuple[SchemaIndexInfo, ...] = ()
    row_counts: dict[str, int] = field(default_factory=dict)
    privacy: dict[str, str] = field(
        default_factory=lambda: {
            "output": "schema metadata and aggregate counts only",
        },
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "db_exists": self.db_exists,
            "tables": [table.to_dict() for table in self.tables],
            "views": [view.to_dict() for view in self.views],
            "indexes": [index.to_dict() for index in self.indexes],
            "row_counts": dict(self.row_counts),
            "privacy": dict(self.privacy),
        }


@dataclass(frozen=True)
class TextIndexDiagnostics:
    """Schema-aware text index diagnostics."""

    text_search_documents_table_exists: bool
    text_search_fts_table_exists: bool
    text_documents_table_exists: bool
    text_documents_count: int
    text_documents_count_kind: str
    text_documents_table: str | None
    text_documents_derived_from: tuple[str, ...]
    text_documents_source_breakdown: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "text_search_documents_table_exists": self.text_search_documents_table_exists,
            "text_search_fts_table_exists": self.text_search_fts_table_exists,
            "text_documents_table_exists": self.text_documents_table_exists,
            "text_documents_count": self.text_documents_count,
            "text_documents_count_kind": self.text_documents_count_kind,
            "text_documents_table": self.text_documents_table,
            "text_documents_derived_from": list(self.text_documents_derived_from),
            "text_documents_source_breakdown": dict(self.text_documents_source_breakdown),
        }


@dataclass(frozen=True)
class EmbeddingDiagnostics:
    """Schema-aware embedding diagnostics."""

    embeddings_table_exists: bool
    embeddings_count: int
    embeddings_count_kind: str
    embeddings_derived_from: tuple[str, ...]
    embedding_source_breakdown_available: bool
    embedding_source_breakdown: dict[str, int] = field(default_factory=dict)
    embedding_model_breakdown: dict[str, int] = field(default_factory=dict)
    embedding_model_source_breakdown: dict[str, dict[str, int]] = field(default_factory=dict)
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "embeddings_table_exists": self.embeddings_table_exists,
            "embeddings_count": self.embeddings_count,
            "embeddings_count_kind": self.embeddings_count_kind,
            "embeddings_derived_from": list(self.embeddings_derived_from),
            "embedding_source_breakdown_available": self.embedding_source_breakdown_available,
            "embedding_source_breakdown": dict(self.embedding_source_breakdown),
            "embedding_model_breakdown": dict(self.embedding_model_breakdown),
            "embedding_model_source_breakdown": {
                model_id: dict(counts)
                for model_id, counts in self.embedding_model_source_breakdown.items()
            },
            "reason": self.reason,
        }


@dataclass(frozen=True)
class MediaAnnotationDiagnostics:
    """Schema-aware media annotation diagnostics."""

    media_annotations_count: int
    media_annotations_in_text_index_count: int
    media_annotations_embedding_count: int
    media_annotations_searchable: bool | None
    media_annotations_searchable_via: tuple[str, ...]
    photo_evidence_retrievable: bool | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "media_annotations_count": self.media_annotations_count,
            "media_annotations_in_text_index_count": self.media_annotations_in_text_index_count,
            "media_annotations_embedding_count": self.media_annotations_embedding_count,
            "media_annotations_searchable": self.media_annotations_searchable,
            "media_annotations_searchable_via": list(self.media_annotations_searchable_via),
            "photo_evidence_retrievable": self.photo_evidence_retrievable,
        }


@dataclass(frozen=True)
class SourceCoverageDiagnostics:
    """Schema-aware source coverage diagnostics."""

    line_messages_count: int
    notes_count: int
    media_items_count: int
    media_annotations_count: int
    text: TextIndexDiagnostics
    embeddings: EmbeddingDiagnostics
    media_annotations: MediaAnnotationDiagnostics

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_messages_count": self.line_messages_count,
            "notes_count": self.notes_count,
            "media_items_count": self.media_items_count,
            "media_annotations_count": self.media_annotations_count,
            "text": self.text.to_dict(),
            "embeddings": self.embeddings.to_dict(),
            "media_annotations": self.media_annotations.to_dict(),
        }


@dataclass(frozen=True)
class RetrievalStageDiagnostics:
    """Privacy-safe per-query retrieval stage counts."""

    query_label: str
    requested_sources: tuple[str, ...]
    fts_candidate_count: int
    exact_like_candidate_count: int
    keyword_like_candidate_count: int
    text_candidate_count: int
    semantic_candidate_count: int
    media_annotation_candidate_count: int
    candidate_count_after_source_filter: int
    candidate_count_after_ranking: int
    final_evidence_count: int
    evidence_source_counts: dict[str, int] = field(default_factory=dict)
    source_stage_counts: dict[str, dict[str, Any]] = field(default_factory=dict)
    fallback_used: bool = False
    fallback_reason: str | None = None
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_label": self.query_label,
            "requested_sources": list(self.requested_sources),
            "fts_candidate_count": self.fts_candidate_count,
            "exact_like_candidate_count": self.exact_like_candidate_count,
            "keyword_like_candidate_count": self.keyword_like_candidate_count,
            "text_candidate_count": self.text_candidate_count,
            "semantic_candidate_count": self.semantic_candidate_count,
            "media_annotation_candidate_count": self.media_annotation_candidate_count,
            "candidate_count_after_source_filter": self.candidate_count_after_source_filter,
            "candidate_count_after_ranking": self.candidate_count_after_ranking,
            "final_evidence_count": self.final_evidence_count,
            "evidence_source_counts": dict(self.evidence_source_counts),
            "source_stage_counts": {
                source: dict(counts)
                for source, counts in self.source_stage_counts.items()
            },
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class RetrievalCoverageSummary:
    """Real-vs-fallback evidence coverage for audit queries."""

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
class RetrievalAuditReport:
    """Privacy-safe retrieval audit report."""

    db_exists: bool
    source_coverage: SourceCoverageDiagnostics | None = None
    retrieval_coverage: RetrievalCoverageSummary = field(default_factory=RetrievalCoverageSummary)
    query_diagnostics: tuple[RetrievalStageDiagnostics, ...] = ()
    selected_semantic_model_id: str | None = None
    selected_semantic_model_has_embeddings: bool | None = None
    warnings: tuple[str, ...] = ()
    privacy: dict[str, str] = field(
        default_factory=lambda: {
            "output": "schema metadata, aggregate counts, stage counts, and safe ids only",
            "redacted": "query text, snippets, filenames, paths, GPS, EXIF, OCR, messages, notes, captions",
        },
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "db_exists": self.db_exists,
            "source_coverage": None
            if self.source_coverage is None
            else self.source_coverage.to_dict(),
            "retrieval_coverage": self.retrieval_coverage.to_dict(),
            "query_diagnostics": [item.to_dict() for item in self.query_diagnostics],
            "selected_semantic_model_id": self.selected_semantic_model_id,
            "selected_semantic_model_has_embeddings": self.selected_semantic_model_has_embeddings,
            "warnings": list(self.warnings),
            "privacy": dict(self.privacy),
        }


def inspect_database_schema(db_path: Path | str) -> DatabaseSchemaReport:
    """Inspect SQLite schema without reading private payload rows."""

    path = Path(db_path).expanduser()
    if not path.exists():
        return DatabaseSchemaReport(db_exists=False)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        table_names = _object_names(connection, "table")
        view_names = _object_names(connection, "view")
        indexes = _inspect_indexes(connection)
        row_counts = {
            table: _count_known_table(connection, table)
            for table in KNOWN_ROW_COUNT_TABLES
            if _table_exists(connection, table)
        }
        return DatabaseSchemaReport(
            db_exists=True,
            tables=tuple(
                SchemaTableInfo(
                    name=table,
                    columns=_columns(connection, table),
                    indexes=tuple(
                        index.name for index in indexes if index.table_name == table
                    ),
                    row_count=row_counts.get(table),
                )
                for table in table_names
                if not table.startswith("sqlite_")
            ),
            views=tuple(
                SchemaViewInfo(name=view, columns=_columns(connection, view))
                for view in view_names
                if not view.startswith("sqlite_")
            ),
            indexes=indexes,
            row_counts=row_counts,
        )
    finally:
        connection.close()


def inspect_source_coverage(db_path: Path | str) -> SourceCoverageDiagnostics:
    """Return schema-aware source and index coverage counts."""

    connection = sqlite3.connect(Path(db_path).expanduser())
    connection.row_factory = sqlite3.Row
    try:
        line_count = _count_known_table(connection, "line_messages", "is_excluded = 0")
        notes_count = _count_known_table(connection, "notes", "is_excluded = 0")
        media_count = _count_known_table(connection, "media_items", "is_excluded = 0")
        annotation_count = _count_known_table(
            connection,
            "media_annotations",
            "is_excluded = 0 AND annotation_type = 'vision'",
        )
        text = _text_index_diagnostics(connection)
        embeddings = _embedding_diagnostics(connection)
        media = _media_annotation_diagnostics(connection, annotation_count)
        return SourceCoverageDiagnostics(
            line_messages_count=line_count,
            notes_count=notes_count,
            media_items_count=media_count,
            media_annotations_count=annotation_count,
            text=text,
            embeddings=embeddings,
            media_annotations=media,
        )
    finally:
        connection.close()


def run_retrieval_audit(
    db_path: Path | str,
    queries: tuple[tuple[str, str, tuple[str, ...]], ...] = (),
    *,
    limit: int = 5,
    selected_semantic_model_id: str | None = None,
) -> RetrievalAuditReport:
    """Audit retrieval stages without printing private query or source text."""

    path = Path(db_path).expanduser()
    if not path.exists():
        return RetrievalAuditReport(
            db_exists=False,
            warnings=("SQLite DB does not exist.",),
        )
    coverage = inspect_source_coverage(path)
    warnings: list[str] = []
    if coverage.embeddings.embeddings_count and not queries:
        warnings.append("embeddings exist but semantic retrieval is not enabled in this audit path")
    diagnostics = tuple(
        _diagnose_query(path, query_label=label, query=query, sources=sources, limit=limit)
        for label, query, sources in queries
    )
    if coverage.embeddings.embeddings_count:
        warnings.append("embeddings exist but semantic retrieval is not enabled in this audit path")
    has_selected_embeddings: bool | None = None
    if selected_semantic_model_id:
        has_selected_embeddings = (
            selected_semantic_model_id in coverage.embeddings.embedding_model_breakdown
        )
        if not has_selected_embeddings:
            warnings.append("selected semantic model has no persisted embeddings")
    return RetrievalAuditReport(
        db_exists=True,
        source_coverage=coverage,
        retrieval_coverage=_coverage_summary_from_diagnostics(diagnostics),
        query_diagnostics=diagnostics,
        selected_semantic_model_id=selected_semantic_model_id,
        selected_semantic_model_has_embeddings=has_selected_embeddings,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def diagnose_retrieval_query(
    db_path: Path | str,
    *,
    query_label: str,
    query: str,
    sources: tuple[str, ...],
    limit: int = 5,
) -> RetrievalStageDiagnostics:
    """Return stage counts for one query without exposing query text."""

    return _diagnose_query(
        Path(db_path).expanduser(),
        query_label=query_label,
        query=query,
        sources=sources,
        limit=limit,
    )


def format_database_schema_report(report: DatabaseSchemaReport) -> str:
    """Format schema report without row payloads."""

    if not report.db_exists:
        return "DB schema: db_exists=false"
    lines = [
        "DB schema: db_exists=true",
        "tables=" + ",".join(table.name for table in report.tables),
    ]
    if report.views:
        lines.append("views=" + ",".join(view.name for view in report.views))
    if report.row_counts:
        counts = "; ".join(
            f"{table}={count}" for table, count in sorted(report.row_counts.items())
        )
        lines.append(f"row_counts: {counts}")
    lines.append("columns:")
    for table in report.tables:
        lines.append(f"  {table.name}: {','.join(table.columns)}")
    lines.append("indexes:")
    if report.indexes:
        for index in report.indexes:
            table_name = index.table_name or "<unknown>"
            columns = ",".join(index.columns) if index.columns else "<unknown>"
            lines.append(f"  {index.name}: table={table_name}; columns={columns}")
    else:
        lines.append("  none")
    lines.append("privacy: schema metadata and aggregate counts only; no row payloads printed.")
    return "\n".join(lines)


def report_to_json(report: DatabaseSchemaReport | RetrievalAuditReport) -> str:
    """Serialize a diagnostics report as deterministic JSON."""

    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def _diagnose_query(
    db_path: Path,
    *,
    query_label: str,
    query: str,
    sources: tuple[str, ...],
    limit: int,
) -> RetrievalStageDiagnostics:
    source_filter = {source for source in sources if source}
    source_stage_counts = _source_stage_counts(db_path, query, source_filter, limit=max(limit * 3, 1))
    requested_source_counts = [
        counts
        for source, counts in source_stage_counts.items()
        if not source_filter or source in source_filter
    ]
    fts_count = sum(int(counts["fts_candidate_count"]) for counts in requested_source_counts)
    exact_like_count = sum(int(counts["exact_like_candidate_count"]) for counts in requested_source_counts)
    keyword_like_count = sum(int(counts["keyword_like_candidate_count"]) for counts in requested_source_counts)
    text_count = sum(int(counts["text_candidate_count"]) for counts in requested_source_counts)
    text_after_source_filter = sum(
        int(counts["candidate_count_after_source_filter"])
        for counts in requested_source_counts
    )
    media_count = 0
    if not source_filter or "photos" in source_filter:
        media_count = _matching_media_annotation_count(db_path, query)
    service = RetrievalService(db_path, ensure_index=False)
    result = service.retrieve(
        query,
        filters=RetrievalFilters(sources=sources),
        limit=max(limit, 1),
        redact_for_display=True,
    )
    after_filter = text_after_source_filter + media_count
    final_count = len(result.evidence)
    evidence_source_counts = _evidence_source_counts(result.evidence)
    source_stage_counts = _with_evidence_counts_and_reasons(
        source_stage_counts,
        source_filter=source_filter,
        evidence_source_counts=evidence_source_counts,
    )
    warnings = ()
    if final_count == 0:
        warnings = ("no real evidence returned for this query",)
    return RetrievalStageDiagnostics(
        query_label=query_label,
        requested_sources=sources,
        fts_candidate_count=fts_count,
        exact_like_candidate_count=exact_like_count,
        keyword_like_candidate_count=keyword_like_count,
        text_candidate_count=text_count,
        semantic_candidate_count=0,
        media_annotation_candidate_count=media_count,
        candidate_count_after_source_filter=after_filter,
        candidate_count_after_ranking=final_count,
        final_evidence_count=final_count,
        evidence_source_counts=evidence_source_counts,
        source_stage_counts=source_stage_counts,
        fallback_used=False,
        warnings=warnings,
    )


def _source_stage_counts(
    db_path: Path,
    query: str,
    source_filter: set[str],
    *,
    limit: int,
) -> dict[str, dict[str, Any]]:
    source_tables = {
        "line": "line_messages",
        "notes": "notes",
        "photos": "media_items",
    }
    counts: dict[str, dict[str, Any]] = {}
    for source, source_table in source_tables.items():
        diagnostics = diagnose_text_search(
            db_path,
            query,
            limit=limit,
            ensure_fts=False,
            source_tables=(source_table,),
        )
        requested = not source_filter or source in source_filter
        text_candidate_count = diagnostics.final_candidate_count
        counts[source] = {
            "fts_candidate_count": diagnostics.fts_candidate_count,
            "exact_like_candidate_count": diagnostics.exact_like_candidate_count,
            "keyword_like_candidate_count": diagnostics.keyword_like_candidate_count,
            "text_candidate_count": text_candidate_count,
            "candidate_count_after_source_filter": text_candidate_count if requested else 0,
            "candidate_count_after_ranking": 0,
            "evidence_conversion_count": 0,
            "drop_reason": "source_not_requested" if not requested else None,
        }
    return counts


def _with_evidence_counts_and_reasons(
    source_stage_counts: dict[str, dict[str, Any]],
    *,
    source_filter: set[str],
    evidence_source_counts: dict[str, int],
) -> dict[str, dict[str, Any]]:
    updated: dict[str, dict[str, Any]] = {}
    for source, counts in source_stage_counts.items():
        row = dict(counts)
        evidence_count = int(evidence_source_counts.get(source, 0))
        row["candidate_count_after_ranking"] = evidence_count
        row["evidence_conversion_count"] = evidence_count
        requested = not source_filter or source in source_filter
        if not requested:
            row["drop_reason"] = "source_not_requested"
        elif evidence_count > 0:
            row["drop_reason"] = None
        elif int(row["text_candidate_count"]) == 0:
            row["drop_reason"] = "no_text_candidates"
        elif int(row["candidate_count_after_source_filter"]) == 0:
            row["drop_reason"] = "filtered_by_source"
        else:
            row["drop_reason"] = "ranked_out"
        updated[source] = row
    return updated


def _text_index_diagnostics(connection: sqlite3.Connection) -> TextIndexDiagnostics:
    has_text_search = _table_exists(connection, "text_search_documents")
    has_fts = _table_exists(connection, "text_search_fts")
    has_text_documents = _table_exists(connection, "text_documents")
    count = _count_known_table(connection, "text_search_documents", "is_excluded = 0")
    breakdown: dict[str, int] = {}
    if has_text_search and _column_exists(connection, "text_search_documents", "source_table"):
        rows = connection.execute(
            """
            SELECT source_table, COUNT(*) AS count
            FROM text_search_documents
            WHERE is_excluded = 0
            GROUP BY source_table
            ORDER BY source_table
            """,
        ).fetchall()
        breakdown = {str(row["source_table"]): int(row["count"]) for row in rows}
    return TextIndexDiagnostics(
        text_search_documents_table_exists=has_text_search,
        text_search_fts_table_exists=has_fts,
        text_documents_table_exists=has_text_documents,
        text_documents_count=count,
        text_documents_count_kind="physical_table" if has_text_search else "unavailable",
        text_documents_table="text_search_documents" if has_text_search else None,
        text_documents_derived_from=("text_search_documents",) if has_text_search else (),
        text_documents_source_breakdown=breakdown,
    )


def _embedding_diagnostics(connection: sqlite3.Connection) -> EmbeddingDiagnostics:
    if not _table_exists(connection, "embeddings"):
        return EmbeddingDiagnostics(
            embeddings_table_exists=False,
            embeddings_count=0,
            embeddings_count_kind="unavailable",
            embeddings_derived_from=(),
            embedding_source_breakdown_available=False,
            reason="embeddings table does not exist",
        )
    count = _count_known_table(connection, "embeddings", "is_excluded = 0")
    columns = set(_columns(connection, "embeddings"))
    model_breakdown = _embedding_model_breakdown(connection, columns)
    if "source_type" in columns:
        rows = connection.execute(
            """
            SELECT source_type, COUNT(*) AS count
            FROM embeddings
            WHERE is_excluded = 0
            GROUP BY source_type
            ORDER BY source_type
            """,
        ).fetchall()
        return EmbeddingDiagnostics(
            embeddings_table_exists=True,
            embeddings_count=count,
            embeddings_count_kind="physical_table",
            embeddings_derived_from=("embeddings",),
            embedding_source_breakdown_available=True,
            embedding_source_breakdown={str(row["source_type"]): int(row["count"]) for row in rows},
            embedding_model_breakdown=model_breakdown,
            embedding_model_source_breakdown=_embedding_model_source_breakdown(
                connection,
                columns,
                source_column="source_type",
            ),
        )
    if "owner_table" not in columns:
        return EmbeddingDiagnostics(
            embeddings_table_exists=True,
            embeddings_count=count,
            embeddings_count_kind="physical_table",
            embeddings_derived_from=("embeddings",),
            embedding_source_breakdown_available=False,
            embedding_model_breakdown=model_breakdown,
            reason="embeddings table does not store source_type and no join mapping was found",
        )
    rows = connection.execute(
        """
        SELECT owner_table, COUNT(*) AS count
        FROM embeddings
        WHERE is_excluded = 0
        GROUP BY owner_table
        ORDER BY owner_table
        """,
    ).fetchall()
    by_source: dict[str, int] = {}
    unmapped: list[str] = []
    for row in rows:
        owner_table = str(row["owner_table"])
        source = EMBEDDING_OWNER_SOURCE_MAP.get(owner_table)
        if source is None:
            unmapped.append(owner_table)
            continue
        by_source[source] = by_source.get(source, 0) + int(row["count"])
    return EmbeddingDiagnostics(
        embeddings_table_exists=True,
        embeddings_count=count,
        embeddings_count_kind="physical_table",
        embeddings_derived_from=("embeddings.owner_table",),
        embedding_source_breakdown_available=bool(by_source),
        embedding_source_breakdown=by_source,
        embedding_model_breakdown=model_breakdown,
        embedding_model_source_breakdown=_embedding_model_source_breakdown(
            connection,
            columns,
            source_column="owner_table",
            owner_table_mapping=True,
        ),
        reason=None
        if by_source
        else "embeddings table does not store source_type and no join mapping was found",
    )


def _embedding_model_breakdown(
    connection: sqlite3.Connection,
    columns: set[str],
) -> dict[str, int]:
    if "model_id" not in columns:
        return {}
    rows = connection.execute(
        """
        SELECT COALESCE(model_id, '<unknown>') AS model_id, COUNT(*) AS count
        FROM embeddings
        WHERE is_excluded = 0
        GROUP BY COALESCE(model_id, '<unknown>')
        ORDER BY model_id
        """,
    ).fetchall()
    return {str(row["model_id"]): int(row["count"]) for row in rows}


def _embedding_model_source_breakdown(
    connection: sqlite3.Connection,
    columns: set[str],
    *,
    source_column: str,
    owner_table_mapping: bool = False,
) -> dict[str, dict[str, int]]:
    if "model_id" not in columns or source_column not in columns:
        return {}
    rows = connection.execute(
        f"""
        SELECT COALESCE(model_id, '<unknown>') AS model_id,
               {source_column} AS source_value,
               COUNT(*) AS count
        FROM embeddings
        WHERE is_excluded = 0
        GROUP BY COALESCE(model_id, '<unknown>'), {source_column}
        ORDER BY model_id, source_value
        """,
    ).fetchall()
    breakdown: dict[str, dict[str, int]] = {}
    for row in rows:
        source = str(row["source_value"])
        if owner_table_mapping:
            source = EMBEDDING_OWNER_SOURCE_MAP.get(source, source)
        model_counts = breakdown.setdefault(str(row["model_id"]), {})
        model_counts[source] = model_counts.get(source, 0) + int(row["count"])
    return breakdown


def _media_annotation_diagnostics(
    connection: sqlite3.Connection,
    annotation_count: int,
) -> MediaAnnotationDiagnostics:
    text_index_count = 0
    if _table_exists(connection, "text_search_documents"):
        text_index_count = _count_known_table(
            connection,
            "text_search_documents",
            "is_excluded = 0 AND source_table IN ('media_items', 'media_annotations')",
        )
    embedding_count = 0
    if _table_exists(connection, "embeddings") and _column_exists(connection, "embeddings", "owner_table"):
        embedding_count = _count_known_table(
            connection,
            "embeddings",
            "is_excluded = 0 AND owner_table IN ('media_items', 'media_annotations')",
        )
    has_media = _table_exists(connection, "media_items")
    has_annotations = _table_exists(connection, "media_annotations")
    searchable_via: list[str] = []
    if has_media and has_annotations:
        searchable_via.append("direct_media_annotation_retrieval")
    if text_index_count:
        searchable_via.append("text_search_documents")
    if embedding_count:
        searchable_via.append("embeddings")
    return MediaAnnotationDiagnostics(
        media_annotations_count=annotation_count,
        media_annotations_in_text_index_count=text_index_count,
        media_annotations_embedding_count=embedding_count,
        media_annotations_searchable=bool(searchable_via) if has_annotations else None,
        media_annotations_searchable_via=tuple(searchable_via),
        photo_evidence_retrievable=bool(has_media and has_annotations),
    )


def _matching_media_annotation_count(db_path: Path, query: str) -> int:
    normalized = normalize_text(query)
    terms = extract_query_terms(normalized)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        if not _table_exists(connection, "media_annotations") or not _table_exists(connection, "media_items"):
            return 0
        rows = connection.execute(
            """
            SELECT a.value_text, a.data_json
            FROM media_items m
            JOIN media_annotations a ON a.media_item_id = m.id
            WHERE m.is_excluded = 0
              AND a.is_excluded = 0
              AND a.annotation_type = 'vision'
            LIMIT ?
            """,
            (MEDIA_ANNOTATION_AUDIT_SCAN_LIMIT,),
        ).fetchall()
        if not normalized:
            return len(rows)
        count = 0
        for row in rows:
            annotation_text = normalize_text(media_annotation_search_text(row["value_text"], row["data_json"]))
            if normalized in annotation_text or any(term in annotation_text for term in terms):
                count += 1
        return count
    finally:
        connection.close()


def _source_kind_for_table(source_table: str) -> str | None:
    return {
        "line_messages": "line",
        "notes": "notes",
        "media_items": "photos",
        "media_annotations": "photos",
    }.get(source_table)


def _evidence_source_counts(evidence: tuple[Any, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in evidence:
        source_kind = getattr(item, "source_kind", None)
        if not source_kind:
            continue
        counts[str(source_kind)] = counts.get(str(source_kind), 0) + 1
    return counts


def _coverage_summary_from_diagnostics(
    diagnostics: tuple[RetrievalStageDiagnostics, ...],
) -> RetrievalCoverageSummary:
    real_counts = {"photos": 0, "line": 0, "notes": 0}
    fallback_count = 0
    zero_queries = 0
    fallback_only = 0
    mixed = 0
    for item in diagnostics:
        if item.final_evidence_count == 0:
            zero_queries += 1
            continue
        if item.fallback_used:
            fallback_count += item.final_evidence_count
            fallback_only += 1
            continue
        active_sources = {
            source for source, count in item.evidence_source_counts.items() if count > 0
        }
        if len(active_sources) > 1:
            mixed += 1
        for source in real_counts:
            real_counts[source] += int(item.evidence_source_counts.get(source, 0))
    return RetrievalCoverageSummary(
        real_photo_evidence_count=real_counts["photos"],
        real_line_evidence_count=real_counts["line"],
        real_note_evidence_count=real_counts["notes"],
        fallback_evidence_count=fallback_count,
        queries_with_zero_evidence=zero_queries,
        queries_with_only_fallback_evidence=fallback_only,
        queries_with_mixed_sources=mixed,
    )


def _object_names(connection: sqlite3.Connection, object_type: str) -> tuple[str, ...]:
    rows = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = ?
        ORDER BY name
        """,
        (object_type,),
    ).fetchall()
    return tuple(str(row["name"]) for row in rows)


def _inspect_indexes(connection: sqlite3.Connection) -> tuple[SchemaIndexInfo, ...]:
    rows = connection.execute(
        """
        SELECT name, tbl_name
        FROM sqlite_master
        WHERE type = 'index'
        ORDER BY name
        """,
    ).fetchall()
    indexes: list[SchemaIndexInfo] = []
    for row in rows:
        name = str(row["name"])
        table_name = str(row["tbl_name"]) if row["tbl_name"] is not None else None
        columns: tuple[str, ...] = ()
        unique = False
        if table_name and not name.startswith("sqlite_autoindex"):
            try:
                info_rows = connection.execute(f"PRAGMA index_info({name})").fetchall()
                columns = tuple(str(info["name"]) for info in info_rows if info["name"] is not None)
                list_rows = connection.execute(f"PRAGMA index_list({table_name})").fetchall()
                for index_row in list_rows:
                    if str(index_row["name"]) == name:
                        unique = bool(index_row["unique"])
                        break
            except sqlite3.DatabaseError:
                columns = ()
        indexes.append(SchemaIndexInfo(name=name, table_name=table_name, columns=columns, unique=unique))
    return tuple(indexes)


def _columns(connection: sqlite3.Connection, table_name: str) -> tuple[str, ...]:
    try:
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    except sqlite3.DatabaseError:
        return ()
    return tuple(str(row["name"]) for row in rows)


def _column_exists(connection: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    return column_name in set(_columns(connection, table_name))


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type IN ('table', 'view')
          AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _count_known_table(
    connection: sqlite3.Connection,
    table_name: str,
    where: str | None = None,
) -> int:
    if not _table_exists(connection, table_name):
        return 0
    if not _safe_sql_identifier(table_name):
        return 0
    sql = f"SELECT COUNT(*) AS count FROM {table_name}"
    if where:
        sql += f" WHERE {where}"
    try:
        row = connection.execute(sql).fetchone()
    except sqlite3.DatabaseError:
        return 0
    return int(row["count"] if row is not None else 0)


def _safe_sql_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value))
