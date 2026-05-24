"""Unified local evidence retrieval across text and media metadata."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from private_memory_agent.retrieval.embeddings import EmbeddingModel, semantic_search
from private_memory_agent.retrieval.text import (
    extract_query_terms,
    index_text,
    make_snippet,
    media_annotation_search_text,
    normalize_text,
    search_text,
)
from private_memory_agent.storage import Storage, initialize_database

SUPPORTED_EVIDENCE_SOURCES = {"photos", "line", "notes"}
SOURCE_KIND_BY_TABLE = {
    "line_messages": "line",
    "notes": "notes",
    "media_items": "photos",
}
REDACTED_TEXT = "[redacted]"
MEDIA_ANNOTATION_SCAN_LIMIT = 5000


@dataclass(frozen=True)
class Evidence:
    """A normalized local evidence record."""

    evidence_id: str
    source_kind: str
    source_table: str
    source_id: int
    title: str | None
    snippet: str
    occurred_at: str | None
    confidence: float
    score: float
    signals: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, redact_private: bool = True) -> dict[str, Any]:
        title = REDACTED_TEXT if redact_private and self.title else self.title
        snippet = REDACTED_TEXT if redact_private and self.snippet else self.snippet
        return {
            "evidence_id": self.evidence_id,
            "source_kind": self.source_kind,
            "source_table": self.source_table,
            "source_id": self.source_id,
            "title": title,
            "snippet": snippet,
            "occurred_at": self.occurred_at,
            "confidence": self.confidence,
            "score": self.score,
            "signals": list(self.signals),
            "metadata": _display_metadata(self.metadata),
        }


@dataclass(frozen=True)
class RetrievalFilters:
    """Filters for local evidence retrieval."""

    sources: tuple[str, ...] = ()
    since: str | None = None
    until: str | None = None
    boost_terms: tuple[str, ...] = ()
    negative_terms: tuple[str, ...] = ()
    preferred_sources: tuple[str, ...] = ()
    semantic_top_k: int | None = None
    semantic_weight: float = 1.0

    def normalized_sources(self) -> set[str]:
        sources = {source.strip().lower() for source in self.sources if source.strip()}
        unknown = sources - SUPPORTED_EVIDENCE_SOURCES
        if unknown:
            raise ValueError(f"unsupported evidence sources: {sorted(unknown)}")
        return sources


@dataclass(frozen=True)
class RetrievalResult:
    """Evidence retrieval response."""

    question: str
    evidence: tuple[Evidence, ...]
    packed_evidence: str
    redacted: bool
    diagnostics: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "redacted": self.redacted,
            "evidence": [
                item.to_dict(redact_private=self.redacted)
                for item in self.evidence
            ],
            "packed_evidence": self.packed_evidence,
            "diagnostics": dict(self.diagnostics),
        }


class RetrievalService:
    """Combines local retrieval signals into ranked evidence."""

    def __init__(
        self,
        db_path: Path | str,
        *,
        embedding_model: EmbeddingModel | None = None,
        ensure_index: bool = True,
    ) -> None:
        self.db_path = Path(db_path).expanduser()
        self.embedding_model = embedding_model
        self.ensure_index = ensure_index

    def retrieve(
        self,
        question: str,
        *,
        filters: RetrievalFilters | None = None,
        limit: int = 10,
        redact_for_display: bool = True,
    ) -> RetrievalResult:
        if limit <= 0:
            raise ValueError("limit must be positive")
        normalized_question = normalize_text(question)
        active_filters = filters or RetrievalFilters()
        source_filter = active_filters.normalized_sources()

        if self.ensure_index and normalized_question:
            index_text(self.db_path)

        candidates: list[Evidence] = []
        text_candidates: list[Evidence] = []
        semantic_candidates: list[Evidence] = []
        media_candidates: list[Evidence] = []
        if not source_filter or SUPPORTED_EVIDENCE_SOURCES & source_filter:
            text_candidates = self._text_evidence(
                question,
                filters=active_filters,
                source_filter=source_filter,
                limit=limit * 3,
            )
            semantic_limit = active_filters.semantic_top_k or limit * 3
            semantic_candidates = self._semantic_evidence(
                question,
                filters=active_filters,
                source_filter=source_filter,
                limit=semantic_limit,
                semantic_weight=active_filters.semantic_weight,
            )
            candidates.extend(text_candidates)
            candidates.extend(semantic_candidates)
        if not source_filter or "photos" in source_filter:
            media_candidates = self._media_annotation_evidence(
                question,
                filters=active_filters,
                limit=limit * 3,
            )
            candidates.extend(media_candidates)

        ranked = tuple(
            _select_ranked_evidence(
                candidates,
                source_filter=source_filter,
                limit=limit,
                boost_terms=active_filters.boost_terms,
                negative_terms=active_filters.negative_terms,
                preferred_sources=active_filters.preferred_sources,
            ),
        )
        return RetrievalResult(
            question=question,
            evidence=ranked,
            packed_evidence=pack_evidence_for_prompt(ranked, redact_private=redact_for_display),
            redacted=redact_for_display,
            diagnostics={
                "text_candidate_count": len(text_candidates),
                "semantic_candidate_count": len(semantic_candidates),
                "media_annotation_candidate_count": len(media_candidates),
                "candidate_count_after_source_filter": len(candidates),
                "candidate_count_after_ranking": len(ranked),
                "final_evidence_count": len(ranked),
            },
        )

    def _text_evidence(
        self,
        question: str,
        *,
        filters: RetrievalFilters,
        source_filter: set[str],
        limit: int,
    ) -> list[Evidence]:
        requested_tables = _source_tables_for_filter(source_filter)
        if requested_tables:
            results = []
            seen: set[tuple[str, int]] = set()
            for source_table in requested_tables:
                for result in search_text(
                    self.db_path,
                    question,
                    limit=limit,
                    ensure_fts=self.ensure_index,
                    source_tables=(source_table,),
                ):
                    key = (result.source_table, result.source_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(result)
        else:
            results = search_text(self.db_path, question, limit=limit, ensure_fts=self.ensure_index)
        if not results:
            return []
        metadata = _text_source_metadata(self.db_path, results)
        evidence: list[Evidence] = []
        for result in results:
            if result.score <= 0.0:
                continue
            source_kind = SOURCE_KIND_BY_TABLE.get(result.source_table)
            if source_kind is None or (source_filter and source_kind not in source_filter):
                continue
            meta = metadata.get((result.source_table, result.source_id), {})
            occurred_at = _optional_string(meta.get("occurred_at"))
            if not _matches_date_filters(occurred_at, filters):
                continue
            evidence.append(
                Evidence(
                    evidence_id=f"{result.source_table}:{result.source_id}",
                    source_kind=source_kind,
                    source_table=result.source_table,
                    source_id=result.source_id,
                    title=result.title,
                    snippet=result.snippet,
                    occurred_at=occurred_at,
                    confidence=0.75,
                    score=result.score,
                    signals=("fts",),
                    metadata={"retrieval": "text"},
                ),
            )
        return evidence

    def _semantic_evidence(
        self,
        question: str,
        *,
        filters: RetrievalFilters,
        source_filter: set[str],
        limit: int,
        semantic_weight: float,
    ) -> list[Evidence]:
        if self.embedding_model is None:
            return []
        try:
            results = semantic_search(
                self.db_path,
                question,
                self.embedding_model,
                limit=limit,
                source_tables=_source_tables_for_filter(source_filter),
            )
        except (RuntimeError, ValueError, json.JSONDecodeError):
            return []
        metadata = _semantic_source_metadata(self.db_path, results)
        evidence: list[Evidence] = []
        for result in results:
            if result.score <= 0.0:
                continue
            source_kind = SOURCE_KIND_BY_TABLE.get(result.source_table)
            if source_kind is None or (source_filter and source_kind not in source_filter):
                continue
            meta = metadata.get((result.source_table, result.source_id), {})
            occurred_at = _optional_string(meta.get("occurred_at"))
            if not _matches_date_filters(occurred_at, filters):
                continue
            evidence.append(
                Evidence(
                    evidence_id=f"{result.source_table}:{result.source_id}",
                    source_kind=source_kind,
                    source_table=result.source_table,
                    source_id=result.source_id,
                    title=result.title,
                    snippet=result.snippet,
                    occurred_at=occurred_at,
                    confidence=_clamp_confidence(result.score),
                    score=max(0.0, result.score) * 0.9 * max(0.0, semantic_weight),
                    signals=("semantic",),
                    metadata={"retrieval": "semantic", "model_id": self.embedding_model.model_id},
                ),
            )
        return evidence

    def _media_annotation_evidence(
        self,
        question: str,
        *,
        filters: RetrievalFilters,
        limit: int,
    ) -> list[Evidence]:
        normalized_question = normalize_text(question)
        query_terms = extract_query_terms(normalized_question)
        storage = initialize_database(self.db_path)
        try:
            rows = storage.connection.execute(
                """
                SELECT m.id AS media_item_id,
                       m.taken_at,
                       m.modified_at,
                       a.value_text,
                       a.data_json,
                       a.confidence,
                       a.model_id
                FROM media_items m
                JOIN media_annotations a ON a.media_item_id = m.id
                WHERE m.is_excluded = 0
                  AND a.is_excluded = 0
                  AND a.annotation_type = 'vision'
                ORDER BY m.id, a.id
                LIMIT ?
                """,
                (max(limit * 20, MEDIA_ANNOTATION_SCAN_LIMIT),),
            ).fetchall()
            evidence: list[Evidence] = []
            for row in rows:
                occurred_at = row["taken_at"] or row["modified_at"]
                if not _matches_date_filters(occurred_at, filters):
                    continue
                annotation_text = media_annotation_search_text(row["value_text"], row["data_json"])
                normalized_annotation = normalize_text(annotation_text)
                if normalized_question and not _text_matches_query(
                    normalized_annotation,
                    normalized_question,
                    query_terms,
                ):
                    continue
                confidence = _coerce_confidence(row["confidence"], default=0.65)
                evidence.append(
                    Evidence(
                        evidence_id=f"media_items:{int(row['media_item_id'])}",
                        source_kind="photos",
                        source_table="media_items",
                        source_id=int(row["media_item_id"]),
                        title=None,
                        snippet=_make_annotation_snippet(annotation_text, normalized_question),
                        occurred_at=occurred_at,
                        confidence=confidence,
                        score=0.7 * confidence,
                        signals=("media_annotation",),
                        metadata={
                            "retrieval": "media_annotation",
                            "model_id": row["model_id"],
                        },
                    ),
                )
                if len(evidence) >= limit:
                    break
            return evidence
        finally:
            storage.close()


def pack_evidence_for_prompt(
    evidence: tuple[Evidence, ...] | list[Evidence],
    *,
    redact_private: bool = False,
    max_items: int | None = None,
) -> str:
    """Pack evidence into a deterministic prompt block."""

    items = list(evidence[:max_items] if max_items is not None else evidence)
    if not items:
        return "No local evidence retrieved."

    lines = ["Local evidence:"]
    for index, item in enumerate(items, start=1):
        rendered = item.to_dict(redact_private=redact_private)
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
        if rendered["title"]:
            lines.append(f"title: {rendered['title']}")
        if rendered["snippet"]:
            lines.append(f"snippet: {rendered['snippet']}")
    return "\n".join(lines)


def _rank_and_dedupe(candidates: list[Evidence]) -> list[Evidence]:
    merged: dict[tuple[str, int], Evidence] = {}
    for candidate in candidates:
        key = (candidate.source_table, candidate.source_id)
        existing = merged.get(key)
        if existing is None:
            merged[key] = candidate
            continue
        merged[key] = _merge_evidence(existing, candidate)

    ranked = list(merged.values())
    ranked.sort(
        key=lambda item: (
            -item.score,
            -item.confidence,
            item.source_kind,
            item.source_table,
            item.source_id,
        ),
    )
    return ranked


def _select_ranked_evidence(
    candidates: list[Evidence],
    *,
    source_filter: set[str],
    limit: int,
    boost_terms: tuple[str, ...] = (),
    negative_terms: tuple[str, ...] = (),
    preferred_sources: tuple[str, ...] = (),
) -> list[Evidence]:
    ranked = _rank_and_dedupe(candidates)
    ranked = _apply_keyword_score_adjustments(
        ranked,
        boost_terms=boost_terms,
        negative_terms=negative_terms,
        preferred_sources=preferred_sources,
    )
    if not source_filter or len(source_filter) <= 1 or limit <= 1:
        return ranked[:limit]

    selected: list[Evidence] = []
    selected_keys: set[tuple[str, int]] = set()
    for source in sorted(source_filter):
        for item in ranked:
            key = (item.source_table, item.source_id)
            if item.source_kind != source or key in selected_keys:
                continue
            selected.append(item)
            selected_keys.add(key)
            break
        if len(selected) >= limit:
            return selected[:limit]

    for item in ranked:
        key = (item.source_table, item.source_id)
        if key in selected_keys:
            continue
        selected.append(item)
        selected_keys.add(key)
        if len(selected) >= limit:
            break
    return selected[:limit]


def _apply_keyword_score_adjustments(
    evidence: list[Evidence],
    *,
    boost_terms: tuple[str, ...],
    negative_terms: tuple[str, ...],
    preferred_sources: tuple[str, ...],
) -> list[Evidence]:
    boost = _normalized_terms(boost_terms)
    negative = _normalized_terms(negative_terms)
    preferred = {source for source in preferred_sources if source in SUPPORTED_EVIDENCE_SOURCES}
    if not boost and not negative and not preferred:
        return evidence

    adjusted: list[Evidence] = []
    for item in evidence:
        text = normalize_text(" ".join(part for part in (item.title, item.snippet) if part))
        boost_hits = _term_hit_count(text, boost)
        negative_hits = _term_hit_count(text, negative)
        score = item.score
        if boost_hits:
            score += min(0.45, 0.18 * boost_hits)
        if negative_hits:
            score -= min(0.45, 0.22 * negative_hits)
        if item.source_kind in preferred:
            score += 0.08
        signals = list(item.signals)
        metadata = dict(item.metadata)
        if boost_hits:
            signals.append("keyword_boost")
            metadata["keyword_hit_count"] = boost_hits
        if negative_hits:
            signals.append("negative_keyword")
            metadata["negative_keyword_hit_count"] = negative_hits
        if item.source_kind in preferred:
            signals.append("source_preference")
        adjusted.append(
            replace(
                item,
                score=max(0.0, score),
                signals=tuple(dict.fromkeys(signals)),
                metadata=metadata,
            ),
        )

    adjusted.sort(
        key=lambda item: (
            -item.score,
            -item.confidence,
            item.source_kind,
            item.source_table,
            item.source_id,
        ),
    )
    return adjusted


def _normalized_terms(terms: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for term in terms:
        cleaned = normalize_text(str(term))
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return tuple(normalized)


def _term_hit_count(text: str, terms: tuple[str, ...]) -> int:
    if not text or not terms:
        return 0
    return sum(1 for term in terms if term and term in text)


def _source_tables_for_filter(source_filter: set[str]) -> tuple[str, ...]:
    if not source_filter:
        return ()
    tables: list[str] = []
    if "line" in source_filter:
        tables.append("line_messages")
    if "notes" in source_filter:
        tables.append("notes")
    if "photos" in source_filter:
        tables.append("media_items")
    return tuple(tables)


def _merge_evidence(left: Evidence, right: Evidence) -> Evidence:
    signals = tuple(dict.fromkeys((*left.signals, *right.signals)))
    score = left.score + right.score
    confidence = max(left.confidence, right.confidence)
    snippet = left.snippet if len(left.snippet) >= len(right.snippet) else right.snippet
    title = left.title or right.title
    occurred_at = left.occurred_at or right.occurred_at
    metadata = {**left.metadata, **right.metadata}
    metadata["merged_signals"] = list(signals)
    return Evidence(
        evidence_id=left.evidence_id,
        source_kind=left.source_kind,
        source_table=left.source_table,
        source_id=left.source_id,
        title=title,
        snippet=snippet,
        occurred_at=occurred_at,
        confidence=confidence,
        score=score,
        signals=signals,
        metadata=metadata,
    )


def _text_source_metadata(
    db_path: Path | str,
    results: list[Any],
) -> dict[tuple[str, int], dict[str, Any]]:
    storage = initialize_database(db_path)
    try:
        metadata: dict[tuple[str, int], dict[str, Any]] = {}
        for result in results:
            if result.source_table == "line_messages":
                row = storage.connection.execute(
                    "SELECT sent_at AS occurred_at FROM line_messages WHERE id = ?",
                    (result.source_id,),
                ).fetchone()
            elif result.source_table == "notes":
                row = storage.connection.execute(
                    """
                    SELECT COALESCE(updated_at_source, created_at_source, updated_at) AS occurred_at
                    FROM notes
                    WHERE id = ?
                    """,
                    (result.source_id,),
                ).fetchone()
            elif result.source_table == "media_items":
                row = storage.connection.execute(
                    """
                    SELECT COALESCE(taken_at, modified_at, updated_at) AS occurred_at
                    FROM media_items
                    WHERE id = ?
                    """,
                    (result.source_id,),
                ).fetchone()
            else:
                row = None
            if row is not None:
                metadata[(result.source_table, result.source_id)] = {
                    "occurred_at": row["occurred_at"],
                }
        return metadata
    finally:
        storage.close()


def _semantic_source_metadata(
    db_path: Path | str,
    results: list[Any],
) -> dict[tuple[str, int], dict[str, Any]]:
    return _text_source_metadata(db_path, results)


def _make_annotation_snippet(text: str, normalized_question: str) -> str:
    if normalized_question:
        return make_snippet(text, normalized_question)
    return re.sub(r"\s+", " ", text).strip()[:96]


def _text_matches_query(
    normalized_text: str,
    normalized_query: str,
    query_terms: tuple[str, ...],
) -> bool:
    if not normalized_query:
        return True
    if normalized_query in normalized_text:
        return True
    return any(term in normalized_text for term in query_terms)


def _matches_date_filters(value: str | None, filters: RetrievalFilters) -> bool:
    if value is None:
        return filters.since is None and filters.until is None
    if filters.since is not None and value < filters.since:
        return False
    if filters.until is not None and value > filters.until:
        return False
    return True


def _coerce_confidence(value: object, *, default: float) -> float:
    try:
        if value is None:
            return default
        return _clamp_confidence(float(value))
    except (TypeError, ValueError):
        return default


def _clamp_confidence(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _display_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {"retrieval", "model_id", "merged_signals", "sensitive", "privacy_flags"}
    return {key: value for key, value in metadata.items() if key in allowed_keys}
