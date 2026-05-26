"""Temporal multimodal event query helpers.

This module implements a deterministic, privacy-safe path for questions such as
"2025年12月で出かけたのはいつ？". It reads structured local metadata only and
returns dates, counts, IDs, and scores without exposing filenames, paths, GPS, or
raw private text.
"""

from __future__ import annotations

import calendar
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from private_memory_agent.media_timestamps import timestamp_coverage
from private_memory_agent.retrieval.text import media_annotation_search_text, normalize_text

SUPPORTED_TEMPORAL_SOURCES = ("photos", "line", "notes")
DEFAULT_MAX_PHOTOS = 2000
DEFAULT_TOP_DAYS = 8
DEFAULT_OUTING_THRESHOLD = 0.45
DEFAULT_CHUNK_AFTER_DAYS = 45
DEFAULT_LONG_RANGE_DAYS = 180
DEFAULT_TOP_CANDIDATE_DATES = 10
DEFAULT_TOP_EVIDENCE_PER_DATE = 5
DEFAULT_CANDIDATES_PER_LONG_RANGE_CHUNK = 5

OUTING_TERMS = (
    "外出",
    "出かけ",
    "お出かけ",
    "屋外",
    "外",
    "街",
    "道路",
    "道",
    "公園",
    "駅",
    "電車",
    "バス",
    "車",
    "空港",
    "旅行",
    "観光",
    "ホテル",
    "店",
    "店舗",
    "買い物",
    "レストラン",
    "カフェ",
    "料理",
    "食事",
    "会場",
    "イベント",
    "outdoor",
    "outside",
    "street",
    "park",
    "station",
    "train",
    "bus",
    "car",
    "airport",
    "travel",
    "restaurant",
    "cafe",
    "shop",
    "event",
)

LOW_OUTING_TERMS = (
    "スクリーンショット",
    "スクショ",
    "画面",
    "書類",
    "文書",
    "資料",
    "receipt",
    "document",
    "screenshot",
    "screen",
    "slide",
)

OUTING_INTENT_TERMS = (
    "出かけ",
    "外出",
    "どこか行",
    "行った日",
    "お出かけ",
    "旅行",
    "屋外",
)

TEMPORAL_FALLBACK_TERMS = (
    "行った",
    "行く",
    "出かけ",
    "外出",
    "会った",
    "店",
    "駅",
    "旅行",
    "集合",
    "食事",
    "予定",
    "レストラン",
    "カフェ",
    "電車",
    "移動",
)


@dataclass(frozen=True)
class TemporalDateRange:
    """A date range with an exclusive end date."""

    start: date
    end: date
    label: str
    source: str = "deterministic"
    expression: str | None = None
    timezone: str | None = "local"
    confidence: float = 1.0
    parse_warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "label": self.label,
            "source": self.source,
            "expression": self.expression or self.label,
            "timezone": self.timezone,
            "end_exclusive": True,
            "confidence": round(self.confidence, 3),
            "parse_warnings": list(self.parse_warnings),
        }


@dataclass(frozen=True)
class TemporalEventQuery:
    """Structured temporal event query."""

    query_type: str
    date_range: TemporalDateRange
    event_type: str
    preferred_sources: tuple[str, ...]
    primary_tool: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_type": self.query_type,
            "date_range": self.date_range.to_dict(),
            "date_range_source": self.date_range.source,
            "parsed_temporal_expression": self.date_range.expression or self.date_range.label,
            "timezone": self.date_range.timezone,
            "event_type": self.event_type,
            "preferred_sources": list(self.preferred_sources),
            "primary_tool": self.primary_tool,
        }


@dataclass(frozen=True)
class TemporalEvidenceItem:
    """Privacy-safe evidence metadata for temporal answers."""

    evidence_id: str
    source_type: str
    should_use: bool
    evidence_role: str
    specificity: str
    relevance_score: float
    reason_category: str
    occurred_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_type": self.source_type,
            "should_use": self.should_use,
            "evidence_role": self.evidence_role,
            "specificity": self.specificity,
            "relevance_score": round(self.relevance_score, 3),
            "reason_category": self.reason_category,
            "occurred_at": self.occurred_at,
            "used_by_answer": self.evidence_role == "used",
        }


@dataclass(frozen=True)
class PhotoCandidate:
    """A photo candidate read from media metadata."""

    media_item_id: int
    evidence_id: str
    taken_at: str
    day: str
    media_type: str
    has_annotation: bool
    has_location: bool
    annotation_available: bool
    outing_score: float
    reasons: tuple[str, ...]
    should_use: bool


@dataclass(frozen=True)
class DailyEventCluster:
    """Candidate temporal event day."""

    date: str
    photo_count: int
    annotated_photo_count: int
    outing_score: float
    confidence: float
    top_evidence_ids: tuple[str, ...]
    candidate_evidence_ids: tuple[str, ...]
    rejected_evidence_ids: tuple[str, ...]
    line_support_count: int
    notes_support_count: int
    support_evidence_ids: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "photo_count": self.photo_count,
            "annotated_photo_count": self.annotated_photo_count,
            "outing_score": round(self.outing_score, 3),
            "confidence": round(self.confidence, 3),
            "top_evidence_ids": list(self.top_evidence_ids),
            "candidate_evidence_ids": list(self.candidate_evidence_ids),
            "rejected_evidence_ids": list(self.rejected_evidence_ids),
            "line_support_count": self.line_support_count,
            "notes_support_count": self.notes_support_count,
            "support_evidence_ids": list(self.support_evidence_ids),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class TemporalAnswer:
    """Temporal event answer payload."""

    answer_succeeded: bool
    conclusion: str
    confidence: float
    dates: tuple[DailyEventCluster, ...]
    evidence_references: tuple[str, ...]
    used_sources: tuple[str, ...]
    unknowns: tuple[str, ...]

    def to_dict(self, *, show_answer: bool = True) -> dict[str, Any]:
        return {
            "answer_succeeded": self.answer_succeeded,
            "conclusion": self.conclusion if show_answer else None,
            "confidence": round(self.confidence, 3),
            "dates": [item.to_dict() for item in self.dates],
            "evidence_references": list(self.evidence_references),
            "used_sources": list(self.used_sources),
            "unknowns": list(self.unknowns) if show_answer else [],
            "answer_hidden": not show_answer,
            "answer_state": "hidden" if not show_answer else ("unknown" if self.confidence == 0 else "visible"),
            "error_class": None,
            "error_message": None,
        }


@dataclass(frozen=True)
class TemporalEventResult:
    """Full temporal event result."""

    ok: bool
    query: TemporalEventQuery
    answer: TemporalAnswer
    evidence: tuple[TemporalEvidenceItem, ...]
    candidate_dates: tuple[DailyEventCluster, ...]
    diagnostics: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def to_dict(self, *, show_answer: bool = True) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "query": self.query.to_dict(),
            "answer": self.answer.to_dict(show_answer=show_answer),
            "candidate_dates": [item.to_dict() for item in self.candidate_dates],
            "evidence": [item.to_dict() for item in self.evidence],
            "diagnostics": dict(self.diagnostics),
            "warnings": list(self.warnings),
        }


def parse_temporal_event_query(
    text: str,
    *,
    today: date | None = None,
) -> TemporalEventQuery | None:
    """Parse obvious temporal outing/event questions."""

    cleaned = str(text or "").strip()
    if not cleaned:
        return None
    current = today or date.today()
    date_range = _parse_date_range(cleaned, today=current)
    if date_range is None:
        return None
    normalized = normalize_text(cleaned)
    if not any(term in normalized for term in OUTING_INTENT_TERMS):
        return None
    return TemporalEventQuery(
        query_type="temporal_event_search",
        date_range=date_range,
        event_type="outing",
        preferred_sources=SUPPORTED_TEMPORAL_SOURCES,
        primary_tool="photo_date_range_search",
    )


def answer_temporal_event_query(
    question: str,
    *,
    db_path: Path | str,
    today: date | None = None,
    top_days: int = DEFAULT_TOP_DAYS,
    top_candidate_dates: int | None = DEFAULT_TOP_CANDIDATE_DATES,
    top_evidence_per_date: int = DEFAULT_TOP_EVIDENCE_PER_DATE,
    chunk_after_days: int = DEFAULT_CHUNK_AFTER_DAYS,
    long_range_days: int = DEFAULT_LONG_RANGE_DAYS,
    candidates_per_long_range_chunk: int = DEFAULT_CANDIDATES_PER_LONG_RANGE_CHUNK,
    max_photos: int = DEFAULT_MAX_PHOTOS,
    outing_threshold: float = DEFAULT_OUTING_THRESHOLD,
    fallback_terms: tuple[str, ...] | None = None,
) -> TemporalEventResult | None:
    """Run the temporal outing workflow if the question matches."""

    parsed = parse_temporal_event_query(question, today=today)
    if parsed is None:
        return None
    db = Path(db_path).expanduser()
    if not db.exists():
        parsed_range = parsed.date_range.to_dict()
        answer = TemporalAnswer(
            answer_succeeded=True,
            conclusion="対象期間のローカルDBが見つからないため、不明です。",
            confidence=0.0,
            dates=(),
            evidence_references=(),
            used_sources=(),
            unknowns=("SQLite DB が見つかりません。",),
        )
        return TemporalEventResult(
            ok=False,
            query=parsed,
            answer=answer,
            evidence=(),
            candidate_dates=(),
            diagnostics={
                "db_exists": False,
                "parsed_date_range": parsed_range,
                "parsed_date_range_start": parsed_range["start"],
                "parsed_date_range_end": parsed_range["end"],
                "date_range_source": parsed_range["source"],
                "date_range_confidence": parsed_range["confidence"],
                "date_range_parse_warnings": parsed_range["parse_warnings"],
                "parsed_temporal_expression": parsed_range["expression"],
                "timezone": parsed_range["timezone"],
            },
            warnings=("SQLite DB does not exist",),
        )

    range_days = _range_days(parsed.date_range)
    candidate_limit = max(1, int(top_candidate_dates if top_candidate_dates is not None else top_days))
    evidence_limit = max(1, int(top_evidence_per_date))
    chunks = _temporal_date_chunks(
        parsed.date_range,
        chunk_after_days=max(1, int(chunk_after_days)),
    )
    chunking_enabled = len(chunks) > 1
    long_range = range_days > int(long_range_days)
    per_chunk_candidate_limit = max(1, int(candidates_per_long_range_chunk))

    photo_diagnostics = photo_date_range_diagnostics(
        db,
        start=parsed.date_range.start,
        end=parsed.date_range.end,
    )
    photos, chunk_clusters, chunk_reports = _collect_chunked_photo_candidates(
        db,
        chunks=chunks,
        max_photos=max_photos,
        outing_threshold=outing_threshold,
        cap_candidates_per_chunk=per_chunk_candidate_limit if long_range else None,
    )
    ranked_clusters = _rank_clusters(_dedupe_clusters(tuple(chunk_clusters)))
    pre_prune_photo_used_clusters = tuple(
        item for item in ranked_clusters if item.confidence >= outing_threshold
    )
    active_fallback_terms = fallback_terms or TEMPORAL_FALLBACK_TERMS
    fallback = _line_note_support_for_range(
        db,
        start=parsed.date_range.start,
        end=parsed.date_range.end,
        terms=active_fallback_terms,
        limit=20,
    )
    fallback_clusters = (
        _fallback_clusters_from_support(fallback)
        if not pre_prune_photo_used_clusters or not photos
        else ()
    )
    candidates_before_pruning = _dedupe_clusters((*ranked_clusters, *fallback_clusters))
    candidate_clusters = tuple(
        _prune_cluster_evidence(cluster, evidence_limit)
        for cluster in _rank_clusters(candidates_before_pruning)[:candidate_limit]
    )
    photo_used_clusters = tuple(
        item for item in candidate_clusters if item.photo_count > 0 and item.confidence >= outing_threshold
    )
    fallback_used_clusters = tuple(
        item for item in candidate_clusters if item.photo_count == 0 and item.support_evidence_ids
    )
    used_clusters = photo_used_clusters or fallback_used_clusters
    evidence = _build_temporal_evidence(photos, candidate_clusters, used_clusters)
    answer = _build_temporal_answer(parsed, used_clusters, candidate_clusters)
    coverage = timestamp_coverage(db)
    parsed_range = parsed.date_range.to_dict()
    months_covered = _month_keys_for_range(parsed.date_range)
    photo_count_by_month = _photo_count_by_month(
        db,
        start=parsed.date_range.start,
        end=parsed.date_range.end,
        months=months_covered,
    )
    candidate_date_count_by_month = _cluster_count_by_month(candidates_before_pruning, months_covered)
    final_candidate_date_count_by_month = _cluster_count_by_month(candidate_clusters, months_covered)
    support_counts_by_month = _line_note_support_counts_by_month(
        db,
        start=parsed.date_range.start,
        end=parsed.date_range.end,
        terms=active_fallback_terms,
        months=months_covered,
    )
    line_support_count_by_month = support_counts_by_month["line"]
    notes_support_count_by_month = support_counts_by_month["notes"]
    final_candidate_months = tuple(
        month for month, count in final_candidate_date_count_by_month.items() if count > 0
    )
    pruned_months = tuple(
        month
        for month in months_covered
        if candidate_date_count_by_month.get(month, 0) > 0
        and final_candidate_date_count_by_month.get(month, 0) == 0
    )
    final_missing_months = tuple(
        month for month in months_covered if final_candidate_date_count_by_month.get(month, 0) == 0
    )
    pruning_reason = _pruning_reason(
        before=len(candidates_before_pruning),
        after=len(candidate_clusters),
        chunking_enabled=chunking_enabled,
        long_range=long_range,
    )
    diagnostics = {
        "db_exists": True,
        **coverage,
        **photo_diagnostics,
        "parsed_date_range": parsed_range,
        "original_date_range": {
            "start": parsed_range["start"],
            "end": parsed_range["end"],
            "end_exclusive": True,
        },
        "parsed_date_range_start": parsed_range["start"],
        "parsed_date_range_end": parsed_range["end"],
        "date_range_source": parsed_range["source"],
        "date_range_confidence": parsed_range["confidence"],
        "date_range_parse_warnings": parsed_range["parse_warnings"],
        "parsed_temporal_expression": parsed_range["expression"],
        "timezone": parsed_range["timezone"],
        "months_covered": list(months_covered),
        "photo_count_by_month": photo_count_by_month,
        "candidate_date_count_by_month": candidate_date_count_by_month,
        "final_candidate_date_count_by_month": final_candidate_date_count_by_month,
        "line_support_count_by_month": line_support_count_by_month,
        "notes_support_count_by_month": notes_support_count_by_month,
        "pruned_months": list(pruned_months),
        "final_candidate_months": list(final_candidate_months),
        "top_candidate_date_limit": candidate_limit,
        "date_range_days": range_days,
        "chunking_enabled": chunking_enabled,
        "chunk_count": len(chunks),
        "chunk_size": "month" if chunking_enabled else "none",
        "chunks": chunk_reports,
        "candidates_before_pruning": len(candidates_before_pruning),
        "candidates_after_pruning": len(candidate_clusters),
        "top_candidate_dates": candidate_limit,
        "top_evidence_per_date": evidence_limit,
        "evidence_sent_count": len(evidence),
        "pruning_reason": pruning_reason,
        "photo_candidates_examined": len(photos),
        "candidate_day_count": len(candidate_clusters),
        "used_day_count": len(used_clusters),
        "rejected_photo_evidence_count": sum(1 for item in evidence if item.evidence_role == "rejected"),
        "candidate_photo_evidence_count": sum(1 for item in evidence if item.evidence_role == "candidate"),
        "used_evidence_count": len(answer.evidence_references),
        "weak_evidence_separated": True,
        "line_date_support_count": fallback["line_date_support_count"],
        "notes_date_support_count": fallback["notes_date_support_count"],
        "support_evidence_ids": list(fallback["support_evidence_ids"]),
        "fallback_sources_used": list(fallback["fallback_sources_used"]),
    }
    warnings: list[str] = []
    if chunking_enabled:
        warnings.append("broad temporal range was chunked and candidate dates were pruned")
    if len(months_covered) > 1 and len(final_candidate_months) == 1 and final_missing_months:
        warnings.append(
            "Final candidates only cover "
            f"{final_candidate_months[0]} although parsed range includes "
            f"{', '.join(final_missing_months)}."
        )
    if photos and not photo_used_clusters and not fallback_clusters:
        warnings.append("photo candidates were found, but outing evidence was weak")
    if not photos and not fallback_clusters:
        warnings.append("no photos were found in the parsed date range")
    if not photos and fallback_clusters:
        warnings.append("no photos were found in the parsed date range; line/notes fallback found support")
    return TemporalEventResult(
        ok=bool(used_clusters),
        query=parsed,
        answer=answer,
        evidence=evidence,
        candidate_dates=candidate_clusters,
        diagnostics=diagnostics,
        warnings=tuple(warnings),
    )


def search_photos_by_date_range(
    db_path: Path | str,
    *,
    start: date,
    end: date,
    limit: int = DEFAULT_MAX_PHOTOS,
    outing_threshold: float = DEFAULT_OUTING_THRESHOLD,
) -> tuple[PhotoCandidate, ...]:
    """Search photos by capture timestamp without exposing private paths."""

    connection = _connect(db_path)
    try:
        if not _table_exists(connection, "media_items"):
            return ()
        rows = connection.execute(
            """
            SELECT m.id AS media_item_id,
                   m.media_type,
                   m.mime_type,
                   m.taken_at,
                   m.modified_at,
                   m.metadata_json AS media_metadata_json,
                   a.id AS annotation_id,
                   a.value_text,
                   a.data_json,
                   a.confidence
            FROM media_items m
            LEFT JOIN media_annotations a
              ON a.id = (
                SELECT latest.id
                FROM media_annotations latest
                WHERE latest.media_item_id = m.id
                  AND latest.is_excluded = 0
                  AND latest.annotation_type = 'vision'
                ORDER BY latest.id DESC
                LIMIT 1
              )
            WHERE m.is_excluded = 0
              AND m.media_type IN ('image', 'video')
              AND m.taken_at >= ?
              AND m.taken_at < ?
            ORDER BY m.taken_at, m.id
            LIMIT ?
            """,
            (start.isoformat(), end.isoformat(), int(limit)),
        ).fetchall()
        candidates: list[PhotoCandidate] = []
        for row in rows:
            occurred_at = str(row["taken_at"] or "")
            if not occurred_at:
                continue
            annotation_text = media_annotation_search_text(row["value_text"], row["data_json"])
            media_metadata = _decode_json(row["media_metadata_json"])
            has_location = _has_location_metadata(media_metadata)
            score, reasons = score_outing_likelihood(
                annotation_text,
                media_type=str(row["media_type"] or ""),
                mime_type=str(row["mime_type"] or ""),
                has_annotation=row["annotation_id"] is not None,
                has_location=has_location,
            )
            candidates.append(
                PhotoCandidate(
                    media_item_id=int(row["media_item_id"]),
                    evidence_id=f"media_items:{int(row['media_item_id'])}",
                    taken_at=occurred_at,
                    day=_date_part(occurred_at),
                    media_type=str(row["media_type"] or "unknown"),
                    has_annotation=row["annotation_id"] is not None,
                    has_location=has_location,
                    annotation_available=bool(annotation_text.strip()),
                    outing_score=score,
                    reasons=reasons,
                    should_use=score >= outing_threshold,
                ),
            )
        return tuple(candidates)
    finally:
        connection.close()


def photo_date_range_diagnostics(
    db_path: Path | str,
    *,
    start: date,
    end: date,
) -> dict[str, Any]:
    """Return count-only diagnostics for photo date-range search."""

    connection = _connect(db_path)
    try:
        if not _table_exists(connection, "media_items"):
            return {
                "date_range_query_column": "taken_at",
                "date_range_query_status": "media_items_table_missing",
                "photo_candidates_count": 0,
                "annotated_photo_candidates_count": 0,
                "unannotated_photo_candidates_count": 0,
                "candidates_before_media_type_filter": 0,
                "candidates_after_media_type_filter": 0,
                "candidates_before_annotation_filter": 0,
                "candidates_after_annotation_filter": 0,
                "removed_reason_counts": {},
                "nearby_month_counts": {},
            }
        start_text = start.isoformat()
        end_text = end.isoformat()
        before_media_type = _count(
            connection,
            """
            SELECT COUNT(*) AS count
            FROM media_items
            WHERE is_excluded = 0
              AND taken_at >= ?
              AND taken_at < ?
            """,
            (start_text, end_text),
        )
        after_media_type = _count(
            connection,
            """
            SELECT COUNT(*) AS count
            FROM media_items
            WHERE is_excluded = 0
              AND media_type IN ('image', 'video')
              AND taken_at >= ?
              AND taken_at < ?
            """,
            (start_text, end_text),
        )
        annotated = _count(
            connection,
            """
            SELECT COUNT(DISTINCT m.id) AS count
            FROM media_items m
            JOIN media_annotations a ON a.media_item_id = m.id
            WHERE m.is_excluded = 0
              AND a.is_excluded = 0
              AND a.annotation_type = 'vision'
              AND m.media_type IN ('image', 'video')
              AND m.taken_at >= ?
              AND m.taken_at < ?
            """,
            (start_text, end_text),
        )
        unannotated = max(0, after_media_type - annotated)
        coverage = timestamp_coverage(db_path)
        status = "ok"
        if coverage["media_items_total_count"] and coverage["media_items_with_taken_at_count"] == 0:
            status = "missing_taken_at"
        elif before_media_type == 0:
            status = "no_rows_in_date_range"
        elif after_media_type == 0:
            status = "all_removed_by_media_type_filter"
        removed = {}
        if before_media_type > after_media_type:
            removed["unsupported_media_type"] = before_media_type - after_media_type
        return {
            "date_range_query_column": "taken_at",
            "date_range_query_status": status,
            "photo_candidates_count": after_media_type,
            "annotated_photo_candidates_count": annotated,
            "unannotated_photo_candidates_count": unannotated,
            "candidates_before_media_type_filter": before_media_type,
            "candidates_after_media_type_filter": after_media_type,
            "candidates_before_annotation_filter": after_media_type,
            "candidates_after_annotation_filter": after_media_type,
            "removed_reason_counts": removed,
            "nearby_month_counts": _nearby_month_counts(connection, start=start, end=end),
        }
    finally:
        connection.close()


def score_outing_likelihood(
    annotation_text: str,
    *,
    media_type: str = "",
    mime_type: str = "",
    has_annotation: bool = False,
    has_location: bool = False,
) -> tuple[float, tuple[str, ...]]:
    """Score whether a photo likely represents an outing."""

    normalized = normalize_text(annotation_text)
    reasons: list[str] = []
    score = 0.08
    if str(media_type).lower() == "image":
        score += 0.06
        reasons.append("image_media")
    if has_annotation:
        score += 0.08
        reasons.append("annotation_available")
    if has_location:
        score += 0.12
        reasons.append("location_metadata_present")
    positive_hits = _term_hits(normalized, OUTING_TERMS)
    negative_hits = _term_hits(normalized, LOW_OUTING_TERMS)
    if positive_hits:
        score += min(0.55, 0.16 * positive_hits)
        reasons.append("outing_annotation_keyword")
    if negative_hits:
        score -= min(0.5, 0.22 * negative_hits)
        reasons.append("low_outing_document_or_screenshot_keyword")
    if str(mime_type).lower().startswith("image/") and not negative_hits:
        score += 0.04
    return _clamp(score), tuple(dict.fromkeys(reasons or ["weak_metadata_only"]))


def cluster_photo_candidates_by_day(
    db_path: Path | str,
    candidates: tuple[PhotoCandidate, ...] | list[PhotoCandidate],
    *,
    outing_threshold: float = DEFAULT_OUTING_THRESHOLD,
) -> tuple[DailyEventCluster, ...]:
    """Group photo candidates by day and add same-day LINE/notes support counts."""

    by_day: dict[str, list[PhotoCandidate]] = {}
    for candidate in candidates:
        by_day.setdefault(candidate.day, []).append(candidate)
    clusters: list[DailyEventCluster] = []
    for day, items in by_day.items():
        ranked = sorted(items, key=lambda item: (-item.outing_score, item.media_item_id))
        photo_count = len(items)
        annotated_count = sum(1 for item in items if item.has_annotation)
        base_score = max((item.outing_score for item in items), default=0.0)
        burst_bonus = 0.12 if photo_count >= 3 else (0.07 if photo_count >= 2 else 0.0)
        support = _line_note_support_for_day(db_path, day, limit=4) if base_score + burst_bonus >= 0.3 else _empty_support()
        support_bonus = 0.08 if support["line_support_count"] else 0.0
        support_bonus += 0.08 if support["notes_support_count"] else 0.0
        confidence = _clamp(base_score + burst_bonus + support_bonus)
        accepted = confidence >= outing_threshold
        top = tuple(item.evidence_id for item in ranked[:3] if accepted or item.should_use)
        candidate_ids = tuple(
            item.evidence_id
            for item in ranked
            if item.evidence_id not in top and item.outing_score >= 0.25
        )
        rejected_ids = tuple(item.evidence_id for item in ranked if item.evidence_id not in top and item.outing_score < 0.25)
        reason = _cluster_reason(ranked, support, accepted=accepted)
        clusters.append(
            DailyEventCluster(
                date=day,
                photo_count=photo_count,
                annotated_photo_count=annotated_count,
                outing_score=base_score,
                confidence=confidence,
                top_evidence_ids=top,
                candidate_evidence_ids=candidate_ids,
                rejected_evidence_ids=rejected_ids,
                line_support_count=int(support["line_support_count"]),
                notes_support_count=int(support["notes_support_count"]),
                support_evidence_ids=tuple(support["support_evidence_ids"]),
                reason=reason,
            ),
        )
    return tuple(clusters)


def _build_temporal_answer(
    query: TemporalEventQuery,
    used_clusters: tuple[DailyEventCluster, ...],
    candidate_clusters: tuple[DailyEventCluster, ...],
) -> TemporalAnswer:
    if not used_clusters:
        return TemporalAnswer(
            answer_succeeded=True,
            conclusion=(
                f"{query.date_range.label}に外出していた日を特定できる十分な根拠はありません。"
            ),
            confidence=0.0,
            dates=(),
            evidence_references=(),
            used_sources=(),
            unknowns=(
                "写真候補はあっても、外出と判断できる注釈や同日サポートが弱い可能性があります。",
                "写真だけでは外出目的は断定できません。",
            )
            if candidate_clusters
            else ("対象期間に写真、LINE、ノートの外出関連候補が見つかりませんでした。",),
        )
    display_dates = tuple(sorted(used_clusters, key=lambda item: item.date))
    date_text = "、".join(_format_japanese_month_day(item.date) for item in display_dates)
    confidence = _clamp(sum(item.confidence for item in display_dates) / len(display_dates))
    evidence_ids = _unique_ids(
        tuple(
            evidence_id
            for cluster in display_dates
            for evidence_id in (*cluster.top_evidence_ids, *cluster.support_evidence_ids)
        ),
    )
    sources = _sources_for_ids(evidence_ids)
    photo_backed = any(cluster.photo_count > 0 for cluster in display_dates)
    if photo_backed:
        conclusion = f"{query.date_range.label}に外出していた可能性がある日は、{date_text}です。"
        unknowns = ("写真と注釈からの推定であり、外出目的までは断定できません。",)
    else:
        conclusion = (
            f"{query.date_range.label}の写真候補は見つかりませんでしたが、"
            f"LINE/ノートには外出に関係しそうな記録候補がある日は、{date_text}です。"
        )
        confidence = min(confidence, 0.42)
        unknowns = (
            "写真では確認できていません。",
            "LINE/ノートの語句一致による弱い推定であり、外出事実は断定できません。",
        )
    return TemporalAnswer(
        answer_succeeded=True,
        conclusion=conclusion,
        confidence=confidence,
        dates=display_dates,
        evidence_references=evidence_ids,
        used_sources=sources,
        unknowns=unknowns,
    )


def _build_temporal_evidence(
    photos: tuple[PhotoCandidate, ...],
    candidate_clusters: tuple[DailyEventCluster, ...],
    used_clusters: tuple[DailyEventCluster, ...],
) -> tuple[TemporalEvidenceItem, ...]:
    used_ids = _unique_ids(
        tuple(
            evidence_id
            for cluster in used_clusters
            for evidence_id in (*cluster.top_evidence_ids, *cluster.support_evidence_ids)
        ),
    )
    candidate_ids = _unique_ids(
        tuple(
            evidence_id
            for cluster in candidate_clusters
            for evidence_id in (*cluster.top_evidence_ids, *cluster.candidate_evidence_ids)
        ),
    )
    rejected_ids = _unique_ids(
        tuple(
            evidence_id
            for cluster in candidate_clusters
            for evidence_id in cluster.rejected_evidence_ids
        ),
    )
    used_set = set(used_ids)
    candidate_set = set(candidate_ids)
    photo_by_id = {item.evidence_id: item for item in photos}
    ordered_ids = _unique_ids(tuple((*used_ids, *candidate_ids, *rejected_ids)))
    evidence: list[TemporalEvidenceItem] = []
    for evidence_id in ordered_ids:
        photo = photo_by_id.get(evidence_id)
        is_used = evidence_id in used_set
        role = "used" if is_used else ("candidate" if evidence_id in candidate_set else "rejected")
        score = photo.outing_score if photo is not None else 0.35
        evidence.append(
            TemporalEvidenceItem(
                evidence_id=evidence_id,
                source_type=_source_from_evidence_id(evidence_id),
                should_use=is_used,
                evidence_role=role,
                specificity="specific" if is_used and photo is not None else "weak",
                relevance_score=score if is_used else min(score, 0.35),
                reason_category=_reason_category(photo, role),
                occurred_at=photo.taken_at if photo is not None else None,
            ),
        )
    return tuple(evidence)


def _line_note_support_for_day(db_path: Path | str, day: str, *, limit: int) -> dict[str, Any]:
    connection = _connect(db_path)
    try:
        start = day
        end = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
        line_ids: list[str] = []
        note_ids: list[str] = []
        if _table_exists(connection, "line_messages"):
            rows = connection.execute(
                """
                SELECT id
                FROM line_messages
                WHERE is_excluded = 0
                  AND sent_at >= ?
                  AND sent_at < ?
                ORDER BY sent_at, id
                LIMIT ?
                """,
                (start, end, limit),
            ).fetchall()
            line_ids = [f"line_messages:{int(row['id'])}" for row in rows]
        if _table_exists(connection, "notes"):
            rows = connection.execute(
                """
                SELECT id
                FROM notes
                WHERE is_excluded = 0
                  AND COALESCE(updated_at_source, created_at_source, updated_at) >= ?
                  AND COALESCE(updated_at_source, created_at_source, updated_at) < ?
                ORDER BY COALESCE(updated_at_source, created_at_source, updated_at), id
                LIMIT ?
                """,
                (start, end, limit),
            ).fetchall()
            note_ids = [f"notes:{int(row['id'])}" for row in rows]
        return {
            "line_support_count": len(line_ids),
            "notes_support_count": len(note_ids),
            "support_evidence_ids": (*line_ids, *note_ids),
        }
    finally:
        connection.close()


def _line_note_support_for_range(
    db_path: Path | str,
    *,
    start: date,
    end: date,
    terms: tuple[str, ...],
    limit: int,
) -> dict[str, Any]:
    connection = _connect(db_path)
    try:
        start_text = start.isoformat()
        end_text = end.isoformat()
        normalized_terms = tuple(normalize_text(term) for term in terms if normalize_text(term))
        line_ids: list[str] = []
        note_ids: list[str] = []
        by_day: dict[str, dict[str, Any]] = {}
        if _table_exists(connection, "line_messages"):
            rows = connection.execute(
                """
                SELECT id, sent_at, COALESCE(normalized_text, body_text, '') AS searchable_text
                FROM line_messages
                WHERE is_excluded = 0
                  AND sent_at >= ?
                  AND sent_at < ?
                ORDER BY sent_at, id
                LIMIT 2000
                """,
                (start_text, end_text),
            ).fetchall()
            for row in rows:
                if not _text_has_any_term(row["searchable_text"], normalized_terms):
                    continue
                evidence_id = f"line_messages:{int(row['id'])}"
                line_ids.append(evidence_id)
                _add_support_day(by_day, _date_part(str(row["sent_at"])), evidence_id, source="line")
                if len(line_ids) >= limit:
                    break
        if _table_exists(connection, "notes"):
            rows = connection.execute(
                """
                SELECT id,
                       COALESCE(updated_at_source, created_at_source, updated_at) AS occurred_at,
                       COALESCE(normalized_text, title, '') || ' ' || COALESCE(body_text, '') AS searchable_text
                FROM notes
                WHERE is_excluded = 0
                  AND COALESCE(updated_at_source, created_at_source, updated_at) >= ?
                  AND COALESCE(updated_at_source, created_at_source, updated_at) < ?
                ORDER BY COALESCE(updated_at_source, created_at_source, updated_at), id
                LIMIT 2000
                """,
                (start_text, end_text),
            ).fetchall()
            for row in rows:
                if not _text_has_any_term(row["searchable_text"], normalized_terms):
                    continue
                evidence_id = f"notes:{int(row['id'])}"
                note_ids.append(evidence_id)
                _add_support_day(by_day, _date_part(str(row["occurred_at"])), evidence_id, source="notes")
                if len(note_ids) >= limit:
                    break
        source_used: list[str] = []
        if line_ids:
            source_used.append("line")
        if note_ids:
            source_used.append("notes")
        return {
            "line_date_support_count": len(line_ids),
            "notes_date_support_count": len(note_ids),
            "support_evidence_ids": _unique_ids(tuple((*line_ids, *note_ids))),
            "fallback_sources_used": tuple(source_used),
            "support_by_day": by_day,
        }
    finally:
        connection.close()


def _fallback_clusters_from_support(support: dict[str, Any]) -> tuple[DailyEventCluster, ...]:
    clusters: list[DailyEventCluster] = []
    for day, payload in sorted(support.get("support_by_day", {}).items()):
        ids = tuple(payload.get("evidence_ids", ()))[:4]
        if not ids:
            continue
        line_count = int(payload.get("line", 0))
        note_count = int(payload.get("notes", 0))
        confidence = 0.34 + (0.04 if line_count else 0.0) + (0.04 if note_count else 0.0)
        clusters.append(
            DailyEventCluster(
                date=day,
                photo_count=0,
                annotated_photo_count=0,
                outing_score=0.0,
                confidence=_clamp(confidence),
                top_evidence_ids=ids,
                candidate_evidence_ids=(),
                rejected_evidence_ids=(),
                line_support_count=line_count,
                notes_support_count=note_count,
                support_evidence_ids=ids,
                reason="temporal_line_notes_fallback_support",
            ),
        )
    clusters.sort(key=lambda item: (-item.confidence, item.date))
    return tuple(clusters[:DEFAULT_TOP_DAYS])


def _range_days(date_range: TemporalDateRange) -> int:
    return max(0, (date_range.end - date_range.start).days)


def _temporal_date_chunks(
    date_range: TemporalDateRange,
    *,
    chunk_after_days: int,
) -> tuple[TemporalDateRange, ...]:
    if _range_days(date_range) <= chunk_after_days:
        return (date_range,)
    chunks: list[TemporalDateRange] = []
    cursor = _month_start(date_range.start)
    while cursor < date_range.end:
        next_month = _add_months(cursor, 1)
        start = max(date_range.start, cursor)
        end = min(date_range.end, next_month)
        if start < end:
            label = start.strftime("%Y-%m")
            chunks.append(
                TemporalDateRange(
                    start=start,
                    end=end,
                    label=label,
                    source=date_range.source,
                    expression=f"{date_range.expression or date_range.label}:{label}",
                    timezone=date_range.timezone,
                    confidence=date_range.confidence,
                    parse_warnings=date_range.parse_warnings,
                ),
            )
        cursor = next_month
    return tuple(chunks or (date_range,))


def _collect_chunked_photo_candidates(
    db_path: Path | str,
    *,
    chunks: tuple[TemporalDateRange, ...],
    max_photos: int,
    outing_threshold: float,
    cap_candidates_per_chunk: int | None,
) -> tuple[tuple[PhotoCandidate, ...], tuple[DailyEventCluster, ...], list[dict[str, Any]]]:
    all_photos: list[PhotoCandidate] = []
    all_clusters: list[DailyEventCluster] = []
    reports: list[dict[str, Any]] = []
    seen_photo_ids: set[int] = set()
    for chunk in chunks:
        chunk_photos = search_photos_by_date_range(
            db_path,
            start=chunk.start,
            end=chunk.end,
            limit=max_photos,
            outing_threshold=outing_threshold,
        )
        for photo in chunk_photos:
            if photo.media_item_id in seen_photo_ids:
                continue
            seen_photo_ids.add(photo.media_item_id)
            all_photos.append(photo)
        chunk_clusters = _rank_clusters(
            cluster_photo_candidates_by_day(
                db_path,
                chunk_photos,
                outing_threshold=outing_threshold,
            ),
        )
        if cap_candidates_per_chunk is not None:
            chunk_clusters = chunk_clusters[:cap_candidates_per_chunk]
        all_clusters.extend(chunk_clusters)
        reports.append(
            {
                "start": chunk.start.isoformat(),
                "end": chunk.end.isoformat(),
                "label": chunk.label,
                "photo_candidates_count": len(chunk_photos),
                "candidate_day_count": len(chunk_clusters),
            },
        )
    return tuple(all_photos), tuple(all_clusters), reports


def _rank_clusters(clusters: tuple[DailyEventCluster, ...] | list[DailyEventCluster]) -> tuple[DailyEventCluster, ...]:
    return tuple(
        sorted(
            clusters,
            key=lambda item: (
                -item.confidence,
                -item.outing_score,
                -(item.line_support_count + item.notes_support_count),
                item.date,
            ),
        ),
    )


def _prune_cluster_evidence(cluster: DailyEventCluster, limit: int) -> DailyEventCluster:
    remaining = max(1, limit)
    top = tuple(cluster.top_evidence_ids[:remaining])
    remaining -= len(top)
    support = tuple(cluster.support_evidence_ids[:remaining]) if remaining > 0 else ()
    remaining -= len(support)
    candidate = tuple(cluster.candidate_evidence_ids[:remaining]) if remaining > 0 else ()
    remaining -= len(candidate)
    rejected = tuple(cluster.rejected_evidence_ids[:remaining]) if remaining > 0 else ()
    return DailyEventCluster(
        date=cluster.date,
        photo_count=cluster.photo_count,
        annotated_photo_count=cluster.annotated_photo_count,
        outing_score=cluster.outing_score,
        confidence=cluster.confidence,
        top_evidence_ids=top,
        candidate_evidence_ids=candidate,
        rejected_evidence_ids=rejected,
        line_support_count=cluster.line_support_count,
        notes_support_count=cluster.notes_support_count,
        support_evidence_ids=support,
        reason=cluster.reason,
    )


def _pruning_reason(
    *,
    before: int,
    after: int,
    chunking_enabled: bool,
    long_range: bool,
) -> str:
    if before > after and long_range:
        return "long_range_chunked_and_top_candidates_pruned"
    if before > after:
        return "top_candidates_pruned"
    if chunking_enabled:
        return "range_chunked_without_candidate_pruning"
    return "not_pruned"


def _month_keys_for_range(date_range: TemporalDateRange) -> tuple[str, ...]:
    keys: list[str] = []
    cursor = _month_start(date_range.start)
    while cursor < date_range.end:
        keys.append(cursor.strftime("%Y-%m"))
        cursor = _add_months(cursor, 1)
    return tuple(keys)


def _photo_count_by_month(
    db_path: Path | str,
    *,
    start: date,
    end: date,
    months: tuple[str, ...],
) -> dict[str, int]:
    counts = {month: 0 for month in months}
    connection = _connect(db_path)
    try:
        if not _table_exists(connection, "media_items"):
            return counts
        rows = connection.execute(
            """
            SELECT substr(taken_at, 1, 7) AS month, COUNT(*) AS count
            FROM media_items
            WHERE is_excluded = 0
              AND media_type IN ('image', 'video')
              AND taken_at >= ?
              AND taken_at < ?
            GROUP BY substr(taken_at, 1, 7)
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        for row in rows:
            month = str(row["month"] or "")
            if month in counts:
                counts[month] = int(row["count"] or 0)
        return counts
    finally:
        connection.close()


def _cluster_count_by_month(
    clusters: tuple[DailyEventCluster, ...],
    months: tuple[str, ...],
) -> dict[str, int]:
    counts = {month: 0 for month in months}
    for cluster in clusters:
        month = cluster.date[:7]
        if month in counts:
            counts[month] += 1
    return counts


def _line_note_support_counts_by_month(
    db_path: Path | str,
    *,
    start: date,
    end: date,
    terms: tuple[str, ...],
    months: tuple[str, ...],
) -> dict[str, dict[str, int]]:
    normalized_terms = tuple(normalize_text(term) for term in terms if normalize_text(term))
    result = {
        "line": {month: 0 for month in months},
        "notes": {month: 0 for month in months},
    }
    connection = _connect(db_path)
    try:
        start_text = start.isoformat()
        end_text = end.isoformat()
        if _table_exists(connection, "line_messages"):
            rows = connection.execute(
                """
                SELECT sent_at, COALESCE(normalized_text, body_text, '') AS searchable_text
                FROM line_messages
                WHERE is_excluded = 0
                  AND sent_at >= ?
                  AND sent_at < ?
                ORDER BY sent_at
                LIMIT 50000
                """,
                (start_text, end_text),
            ).fetchall()
            for row in rows:
                if not _text_has_any_term(row["searchable_text"], normalized_terms):
                    continue
                month = _date_part(str(row["sent_at"]))[:7]
                if month in result["line"]:
                    result["line"][month] += 1
        if _table_exists(connection, "notes"):
            rows = connection.execute(
                """
                SELECT COALESCE(updated_at_source, created_at_source, updated_at) AS occurred_at,
                       COALESCE(normalized_text, title, '') || ' ' || COALESCE(body_text, '') AS searchable_text
                FROM notes
                WHERE is_excluded = 0
                  AND COALESCE(updated_at_source, created_at_source, updated_at) >= ?
                  AND COALESCE(updated_at_source, created_at_source, updated_at) < ?
                ORDER BY COALESCE(updated_at_source, created_at_source, updated_at)
                LIMIT 50000
                """,
                (start_text, end_text),
            ).fetchall()
            for row in rows:
                if not _text_has_any_term(row["searchable_text"], normalized_terms):
                    continue
                month = _date_part(str(row["occurred_at"]))[:7]
                if month in result["notes"]:
                    result["notes"][month] += 1
        return result
    finally:
        connection.close()


def _parse_date_range(text: str, *, today: date) -> TemporalDateRange | None:
    month_range = _parse_japanese_month_range(text)
    if month_range is not None:
        return month_range
    year_season = re.search(r"(?P<year>\d{4})\s*年\s*(?P<season>春|夏|秋|冬)", text)
    if year_season:
        return _season_range(
            int(year_season.group("year")),
            year_season.group("season"),
            expression=_clean_expression(year_season.group(0)),
        )
    year_month = re.search(r"(?P<year>\d{4})\s*年\s*(?P<month>\d{1,2})\s*月", text)
    if year_month:
        return _month_range(
            int(year_month.group("year")),
            int(year_month.group("month")),
            expression=_clean_expression(year_month.group(0)),
        )
    slash_month = re.search(r"(?P<year>\d{4})\s*[-/]\s*(?P<month>\d{1,2})(?!\s*[-/]\s*\d)", text)
    if slash_month:
        return _month_range(
            int(slash_month.group("year")),
            int(slash_month.group("month")),
            expression=_clean_expression(slash_month.group(0)),
        )
    last_year_month = re.search(r"去年(?:の)?\s*(?P<month>\d{1,2})\s*月", text)
    if last_year_month:
        return _month_range(
            today.year - 1,
            int(last_year_month.group("month")),
            label_prefix="去年",
            expression=_clean_expression(last_year_month.group(0)),
        )
    if "先月" in text:
        year = today.year
        month = today.month - 1
        if month <= 0:
            year -= 1
            month = 12
        return _month_range(year, month, label="先月", expression="先月")
    if "去年" in text and "夏" in text:
        return _season_range(
            today.year - 1,
            "夏",
            expression="去年の夏",
        )
    year_only = re.search(r"(?P<year>\d{4})\s*年(?!\s*(?:\d{1,2}\s*月|春|夏|秋|冬))", text)
    if year_only:
        year = int(year_only.group("year"))
        return TemporalDateRange(
            date(year, 1, 1),
            date(year + 1, 1, 1),
            f"{year}年",
            expression=_clean_expression(year_only.group(0)),
        )
    return None


def _parse_japanese_month_range(text: str) -> TemporalDateRange | None:
    separator = r"(?:から|〜|~|－|-|以降)"
    with_start_year = re.search(
        rf"(?P<start_year>\d{{4}})\s*年\s*"
        rf"(?P<start_month>\d{{1,2}})\s*月\s*"
        rf"{separator}\s*"
        rf"(?:(?P<end_year>\d{{4}})\s*年\s*)?"
        rf"(?P<end_month>\d{{1,2}})\s*月?(?:\s*まで)?",
        text,
    )
    if with_start_year:
        return _build_month_range_from_match(
            expression=_clean_expression(with_start_year.group(0)),
            start_year=int(with_start_year.group("start_year")),
            start_month=int(with_start_year.group("start_month")),
            end_month=int(with_start_year.group("end_month")),
            end_year=(
                int(with_start_year.group("end_year"))
                if with_start_year.group("end_year")
                else None
            ),
        )

    without_start_year = re.search(
        rf"(?P<start_month>\d{{1,2}})\s*月\s*"
        rf"{separator}\s*"
        rf"(?:(?P<end_year>\d{{4}})\s*年\s*)?"
        rf"(?P<end_month>\d{{1,2}})\s*月?(?:\s*まで)?",
        text,
    )
    if without_start_year:
        years = {int(value) for value in re.findall(r"(\d{4})\s*年", text)}
        end_year = (
            int(without_start_year.group("end_year"))
            if without_start_year.group("end_year")
            else None
        )
        if end_year is not None:
            years.add(end_year)
        if len(years) != 1:
            return None
        return _build_month_range_from_match(
            expression=_clean_expression(without_start_year.group(0)),
            start_year=next(iter(years)),
            start_month=int(without_start_year.group("start_month")),
            end_month=int(without_start_year.group("end_month")),
            end_year=end_year,
            parse_warnings=("start_year_inferred_from_query_context",),
            confidence=0.85,
        )
    return None


def _build_month_range_from_match(
    *,
    expression: str,
    start_year: int,
    start_month: int,
    end_month: int,
    end_year: int | None = None,
    parse_warnings: tuple[str, ...] = (),
    confidence: float = 0.98,
) -> TemporalDateRange | None:
    if start_month < 1 or start_month > 12 or end_month < 1 or end_month > 12:
        return None
    resolved_end_year = end_year if end_year is not None else start_year
    warnings = list(parse_warnings)
    if end_year is None and end_month < start_month:
        resolved_end_year = start_year + 1
        warnings.append("end_year_inferred_as_next_year")
    start = date(start_year, start_month, 1)
    end = _add_months(date(resolved_end_year, end_month, 1), 1)
    if end <= start:
        return None
    if start_year == resolved_end_year:
        label = f"{start_year}年{start_month}月から{end_month}月"
    else:
        label = f"{start_year}年{start_month}月から{resolved_end_year}年{end_month}月"
    return TemporalDateRange(
        start=start,
        end=end,
        label=label,
        expression=expression or label,
        confidence=confidence,
        parse_warnings=tuple(dict.fromkeys(warnings)),
    )


def _season_range(year: int, season: str, *, expression: str | None = None) -> TemporalDateRange | None:
    if season == "春":
        start = date(year, 3, 1)
        end = date(year, 6, 1)
    elif season == "夏":
        start = date(year, 6, 1)
        end = date(year, 9, 1)
    elif season == "秋":
        start = date(year, 9, 1)
        end = date(year, 12, 1)
    elif season == "冬":
        start = date(year, 12, 1)
        end = date(year + 1, 3, 1)
    else:
        return None
    label = f"{year}年{season}"
    return TemporalDateRange(start=start, end=end, label=label, expression=expression or label)


def _month_range(
    year: int,
    month: int,
    *,
    label: str | None = None,
    label_prefix: str | None = None,
    expression: str | None = None,
) -> TemporalDateRange | None:
    if month < 1 or month > 12:
        return None
    last_day = calendar.monthrange(year, month)[1]
    start = date(year, month, 1)
    end = date(year, month, last_day) + timedelta(days=1)
    rendered = label or f"{year}年{month}月"
    if label_prefix:
        rendered = f"{label_prefix}{month}月"
    return TemporalDateRange(start=start, end=end, label=rendered, expression=expression or rendered)


def _empty_support() -> dict[str, Any]:
    return {"line_support_count": 0, "notes_support_count": 0, "support_evidence_ids": ()}


def _add_support_day(
    by_day: dict[str, dict[str, Any]],
    day: str,
    evidence_id: str,
    *,
    source: str,
) -> None:
    payload = by_day.setdefault(day, {"line": 0, "notes": 0, "evidence_ids": []})
    payload[source] = int(payload.get(source, 0)) + 1
    if evidence_id not in payload["evidence_ids"]:
        payload["evidence_ids"].append(evidence_id)


def _text_has_any_term(value: Any, terms: tuple[str, ...]) -> bool:
    text = normalize_text(str(value or ""))
    if not text:
        return False
    return any(term in text for term in terms)


def _dedupe_clusters(clusters: tuple[DailyEventCluster, ...]) -> tuple[DailyEventCluster, ...]:
    by_day: dict[str, DailyEventCluster] = {}
    for cluster in clusters:
        existing = by_day.get(cluster.date)
        if existing is None or cluster.confidence > existing.confidence:
            by_day[cluster.date] = cluster
    return tuple(sorted(by_day.values(), key=lambda item: (-item.confidence, item.date)))


def _clean_expression(value: str) -> str:
    return re.sub(r"\s+", "", value.strip())


def _count(connection: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> int:
    row = connection.execute(sql, params).fetchone()
    return int(row["count"] or 0)


def _nearby_month_counts(
    connection: sqlite3.Connection,
    *,
    start: date,
    end: date,
) -> dict[str, Any]:
    previous_start = _add_months(_month_start(start), -1)
    previous_end = _month_start(start)
    next_start = _month_start(end)
    next_end = _add_months(next_start, 1)
    return {
        "previous_month": previous_start.strftime("%Y-%m"),
        "previous_month_photo_count": _photo_count_between(connection, previous_start, previous_end),
        "current_range": f"{start.isoformat()}..{end.isoformat()}",
        "current_month_photo_count": _photo_count_between(connection, start, end),
        "next_month": next_start.strftime("%Y-%m"),
        "next_month_photo_count": _photo_count_between(connection, next_start, next_end),
    }


def _photo_count_between(connection: sqlite3.Connection, start: date, end: date) -> int:
    return _count(
        connection,
        """
        SELECT COUNT(*) AS count
        FROM media_items
        WHERE is_excluded = 0
          AND media_type IN ('image', 'video')
          AND taken_at >= ?
          AND taken_at < ?
        """,
        (start.isoformat(), end.isoformat()),
    )


def _month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def _add_months(value: date, months: int) -> date:
    month_index = (value.year * 12 + value.month - 1) + months
    year = month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _cluster_reason(
    ranked: list[PhotoCandidate],
    support: dict[str, Any],
    *,
    accepted: bool,
) -> str:
    if not accepted:
        return "weak_photo_annotation_or_metadata"
    reasons: list[str] = []
    if ranked:
        reasons.extend(ranked[0].reasons)
    if support["line_support_count"]:
        reasons.append("same_day_line_support")
    if support["notes_support_count"]:
        reasons.append("same_day_note_support")
    return ",".join(dict.fromkeys(reasons)) or "photo_date_cluster"


def _reason_category(photo: PhotoCandidate | None, role: str) -> str:
    if role == "used":
        if photo and "outing_annotation_keyword" in photo.reasons:
            return "temporal_outing_photo_match"
        return "temporal_outing_day_support"
    if role == "candidate":
        return "examined_candidate_not_used"
    return "weak_or_rejected_temporal_candidate"


def _source_from_evidence_id(evidence_id: str) -> str:
    if evidence_id.startswith("media_items:") or evidence_id.startswith("media_annotations:"):
        return "photos"
    if evidence_id.startswith("line_messages:"):
        return "line"
    if evidence_id.startswith("notes:"):
        return "notes"
    return "unknown"


def _sources_for_ids(evidence_ids: tuple[str, ...]) -> tuple[str, ...]:
    sources: list[str] = []
    for evidence_id in evidence_ids:
        source = _source_from_evidence_id(evidence_id)
        if source != "unknown" and source not in sources:
            sources.append(source)
    return tuple(sources)


def _date_part(value: str) -> str:
    return str(value)[:10]


def _format_japanese_month_day(value: str) -> str:
    parsed = date.fromisoformat(value)
    return f"{parsed.month}月{parsed.day}日"


def _term_hits(text: str, terms: tuple[str, ...]) -> int:
    if not text:
        return 0
    return sum(1 for term in terms if normalize_text(term) in text)


def _decode_json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _has_location_metadata(metadata: dict[str, Any]) -> bool:
    keys = {str(key).lower() for key in metadata}
    if {"gps", "gps_lat", "gps_lon", "latitude", "longitude", "location"} & keys:
        return True
    for value in metadata.values():
        if isinstance(value, dict) and _has_location_metadata(value):
            return True
    return False


def _connect(db_path: Path | str) -> sqlite3.Connection:
    connection = sqlite3.connect(Path(db_path).expanduser())
    connection.row_factory = sqlite3.Row
    return connection


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _unique_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return tuple(result)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
