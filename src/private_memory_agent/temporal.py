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


@dataclass(frozen=True)
class TemporalDateRange:
    """A date range with an exclusive end date."""

    start: date
    end: date
    label: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "label": self.label,
            "end_exclusive": True,
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
    max_photos: int = DEFAULT_MAX_PHOTOS,
    outing_threshold: float = DEFAULT_OUTING_THRESHOLD,
) -> TemporalEventResult | None:
    """Run the temporal outing workflow if the question matches."""

    parsed = parse_temporal_event_query(question, today=today)
    if parsed is None:
        return None
    db = Path(db_path).expanduser()
    if not db.exists():
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
            diagnostics={"db_exists": False},
            warnings=("SQLite DB does not exist",),
        )

    photos = search_photos_by_date_range(
        db,
        start=parsed.date_range.start,
        end=parsed.date_range.end,
        limit=max_photos,
        outing_threshold=outing_threshold,
    )
    clusters = cluster_photo_candidates_by_day(
        db,
        photos,
        outing_threshold=outing_threshold,
    )
    ranked_clusters = tuple(
        sorted(
            clusters,
            key=lambda item: (-item.confidence, item.date),
        )[:top_days],
    )
    used_clusters = tuple(item for item in ranked_clusters if item.confidence >= outing_threshold)
    evidence = _build_temporal_evidence(photos, ranked_clusters, used_clusters)
    answer = _build_temporal_answer(parsed, used_clusters, ranked_clusters)
    coverage = timestamp_coverage(db)
    diagnostics = {
        "db_exists": True,
        **coverage,
        "parsed_date_range": parsed.date_range.to_dict(),
        "photo_candidates_examined": len(photos),
        "candidate_day_count": len(ranked_clusters),
        "used_day_count": len(used_clusters),
        "rejected_photo_evidence_count": sum(1 for item in evidence if item.evidence_role == "rejected"),
        "candidate_photo_evidence_count": sum(1 for item in evidence if item.evidence_role == "candidate"),
        "used_evidence_count": len(answer.evidence_references),
        "weak_evidence_separated": True,
    }
    warnings: list[str] = []
    if photos and not used_clusters:
        warnings.append("photo candidates were found, but outing evidence was weak")
    if not photos:
        warnings.append("no photos were found in the parsed date range")
    return TemporalEventResult(
        ok=bool(used_clusters),
        query=parsed,
        answer=answer,
        evidence=evidence,
        candidate_dates=ranked_clusters,
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
    """Search photos by taken/modified timestamp without exposing private paths."""

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
              AND COALESCE(m.taken_at, m.modified_at) >= ?
              AND COALESCE(m.taken_at, m.modified_at) < ?
            ORDER BY COALESCE(m.taken_at, m.modified_at), m.id
            LIMIT ?
            """,
            (start.isoformat(), end.isoformat(), int(limit)),
        ).fetchall()
        candidates: list[PhotoCandidate] = []
        for row in rows:
            occurred_at = str(row["taken_at"] or row["modified_at"] or "")
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
            else ("対象期間に写真候補が見つかりませんでした。",),
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
    return TemporalAnswer(
        answer_succeeded=True,
        conclusion=(
            f"{query.date_range.label}に外出していた可能性がある日は、{date_text}です。"
        ),
        confidence=confidence,
        dates=display_dates,
        evidence_references=evidence_ids,
        used_sources=sources,
        unknowns=("写真と注釈からの推定であり、外出目的までは断定できません。",),
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
        score = photo.outing_score if photo is not None else 0.6
        evidence.append(
            TemporalEvidenceItem(
                evidence_id=evidence_id,
                source_type=_source_from_evidence_id(evidence_id),
                should_use=is_used,
                evidence_role=role,
                specificity="specific" if is_used else ("weak" if role == "candidate" else "weak"),
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


def _parse_date_range(text: str, *, today: date) -> TemporalDateRange | None:
    year_month = re.search(r"(?P<year>\d{4})\s*年\s*(?P<month>\d{1,2})\s*月", text)
    if year_month:
        return _month_range(int(year_month.group("year")), int(year_month.group("month")))
    slash_month = re.search(r"(?P<year>\d{4})\s*[-/]\s*(?P<month>\d{1,2})(?!\s*[-/]\s*\d)", text)
    if slash_month:
        return _month_range(int(slash_month.group("year")), int(slash_month.group("month")))
    last_year_month = re.search(r"去年(?:の)?\s*(?P<month>\d{1,2})\s*月", text)
    if last_year_month:
        return _month_range(today.year - 1, int(last_year_month.group("month")), label_prefix="去年")
    if "先月" in text:
        year = today.year
        month = today.month - 1
        if month <= 0:
            year -= 1
            month = 12
        return _month_range(year, month, label="先月")
    if "去年" in text and "夏" in text:
        year = today.year - 1
        return TemporalDateRange(date(year, 6, 1), date(year, 9, 1), f"{year}年夏")
    return None


def _month_range(
    year: int,
    month: int,
    *,
    label: str | None = None,
    label_prefix: str | None = None,
) -> TemporalDateRange | None:
    if month < 1 or month > 12:
        return None
    last_day = calendar.monthrange(year, month)[1]
    start = date(year, month, 1)
    end = date(year, month, last_day) + timedelta(days=1)
    rendered = label or f"{year}年{month}月"
    if label_prefix:
        rendered = f"{label_prefix}{month}月"
    return TemporalDateRange(start=start, end=end, label=rendered)


def _empty_support() -> dict[str, Any]:
    return {"line_support_count": 0, "notes_support_count": 0, "support_evidence_ids": ()}


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
