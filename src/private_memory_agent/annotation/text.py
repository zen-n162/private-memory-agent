"""Japanese text understanding annotation pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from private_memory_agent.runtime import (
    TextUnderstandingClient,
    TextUnderstandingRequest,
    TextUnderstandingResponse,
)
from private_memory_agent.storage import Storage, initialize_database

ANNOTATION_TYPE = "understanding"
SUPPORTED_TEXT_SOURCES = {"line", "notes"}
REQUIRED_EXTRACTION_KEYS = {
    "entities",
    "topics",
    "dates",
    "action_items",
    "event_hints",
    "summary",
    "confidence",
}


class TextExtractionError(ValueError):
    """Raised when model extraction JSON is invalid."""


@dataclass(frozen=True)
class ExtractedTextUnderstanding:
    """Validated structured extraction."""

    entities: tuple[dict[str, Any], ...]
    topics: tuple[str, ...]
    dates: tuple[dict[str, Any], ...]
    action_items: tuple[dict[str, Any], ...]
    event_hints: tuple[dict[str, Any], ...]
    summary: str
    confidence: float
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TextAnnotationResult:
    """Summary-only result safe for CLI output."""

    source: str
    selected: int = 0
    annotated: int = 0
    skipped_empty: int = 0
    skipped_already_annotated: int = 0
    errors: int = 0
    model_id: str = "unknown-text-model"


def annotate_text(
    db_path: Path | str,
    *,
    source: str,
    client: TextUnderstandingClient,
    model_id: str,
    limit: int | None = None,
    batch_size: int = 8,
) -> TextAnnotationResult:
    """Annotate LINE or note text with validated structured extraction."""

    source_key = _normalize_source(source)
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    storage = initialize_database(db_path)
    selected = 0
    annotated = 0
    skipped_empty = 0
    skipped_already_annotated = 0
    errors = 0
    try:
        candidates = select_unannotated_text_items(
            storage,
            source=source_key,
            model_id=model_id,
            limit=limit,
        )
        selected = len(candidates)
        for batch in _batched(candidates, batch_size):
            for item in batch:
                source_table = str(item["source_table"])
                source_id = int(item["source_id"])
                text = str(item.get("text") or "").strip()
                if not text:
                    skipped_empty += 1
                    continue
                if _has_text_annotation(storage, source_table, source_id, model_id):
                    skipped_already_annotated += 1
                    continue
                try:
                    response = client.understand(
                        TextUnderstandingRequest(
                            text=text,
                            source_type=source_key,
                            source_id=source_id,
                            model=model_id,
                        ),
                    )
                    extraction = parse_text_understanding_response(response)
                    _store_text_annotation(
                        storage,
                        source_table=source_table,
                        source_id=source_id,
                        model_id=model_id,
                        extraction=extraction,
                    )
                    annotated += 1
                except (RuntimeError, TextExtractionError, ValueError):
                    errors += 1
                    continue
    finally:
        storage.close()

    return TextAnnotationResult(
        source=source_key,
        selected=selected,
        annotated=annotated,
        skipped_empty=skipped_empty,
        skipped_already_annotated=skipped_already_annotated,
        errors=errors,
        model_id=model_id,
    )


def select_unannotated_text_items(
    storage: Storage,
    *,
    source: str,
    model_id: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Select LINE messages or notes without an active understanding annotation."""

    source_key = _normalize_source(source)
    if source_key == "line":
        query = _line_selection_query(limit)
        params: tuple[Any, ...] = ("line_messages", ANNOTATION_TYPE, model_id)
    else:
        query = _note_selection_query(limit)
        params = ("notes", ANNOTATION_TYPE, model_id)
    if limit is not None:
        params = (*params, limit)
    rows = storage.connection.execute(query, params).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def parse_text_understanding_response(
    response: TextUnderstandingResponse,
) -> ExtractedTextUnderstanding:
    """Parse and strictly validate model JSON output."""

    try:
        payload = json.loads(response.json_text)
    except json.JSONDecodeError as exc:
        raise TextExtractionError("text understanding response is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise TextExtractionError("text understanding JSON must be an object")
    keys = set(payload)
    if keys != REQUIRED_EXTRACTION_KEYS:
        missing = REQUIRED_EXTRACTION_KEYS - keys
        extra = keys - REQUIRED_EXTRACTION_KEYS
        raise TextExtractionError(
            "text understanding JSON keys mismatch; "
            f"missing={sorted(missing)} extra={sorted(extra)}",
        )

    entities = tuple(_validate_entity(item) for item in _require_list(payload, "entities"))
    topics = tuple(
        _require_nonempty_string(item, "topics[]")
        for item in _require_list(payload, "topics")
    )
    dates = tuple(_validate_date(item) for item in _require_list(payload, "dates"))
    action_items = tuple(
        _validate_action_item(item) for item in _require_list(payload, "action_items")
    )
    event_hints = tuple(
        _validate_event_hint(item) for item in _require_list(payload, "event_hints")
    )
    summary = _require_string(payload.get("summary"), "summary")
    confidence = _require_confidence(payload.get("confidence"), "confidence")

    return ExtractedTextUnderstanding(
        entities=entities,
        topics=topics,
        dates=dates,
        action_items=action_items,
        event_hints=event_hints,
        summary=summary,
        confidence=confidence,
        raw=payload,
    )


def _store_text_annotation(
    storage: Storage,
    *,
    source_table: str,
    source_id: int,
    model_id: str,
    extraction: ExtractedTextUnderstanding,
) -> int:
    with storage.transaction():
        if _has_text_annotation(storage, source_table, source_id, model_id):
            return 0
        return storage.text_annotations.insert_text_annotation(
            source_table=source_table,
            source_id=source_id,
            annotation_type=ANNOTATION_TYPE,
            model_id=model_id,
            summary=extraction.summary,
            entities_json=json.dumps(extraction.entities, ensure_ascii=False, sort_keys=True),
            topics_json=json.dumps(extraction.topics, ensure_ascii=False, sort_keys=True),
            dates_json=json.dumps(extraction.dates, ensure_ascii=False, sort_keys=True),
            action_items_json=json.dumps(
                extraction.action_items,
                ensure_ascii=False,
                sort_keys=True,
            ),
            event_hints_json=json.dumps(
                extraction.event_hints,
                ensure_ascii=False,
                sort_keys=True,
            ),
            confidence=extraction.confidence,
            raw_json=json.dumps(extraction.raw, ensure_ascii=False, sort_keys=True),
        )


def _has_text_annotation(
    storage: Storage,
    source_table: str,
    source_id: int,
    model_id: str,
) -> bool:
    row = storage.connection.execute(
        """
        SELECT 1
        FROM text_annotations
        WHERE source_table = ?
          AND source_id = ?
          AND annotation_type = ?
          AND model_id = ?
          AND is_excluded = 0
        LIMIT 1
        """,
        (source_table, source_id, ANNOTATION_TYPE, model_id),
    ).fetchone()
    return row is not None


def _line_selection_query(limit: int | None) -> str:
    limit_clause = "" if limit is None else "LIMIT ?"
    return f"""
        SELECT 'line_messages' AS source_table,
               id AS source_id,
               body_text AS text
        FROM line_messages
        WHERE is_excluded = 0
          AND COALESCE(body_text, '') != ''
          AND NOT EXISTS (
              SELECT 1
              FROM text_annotations a
              WHERE a.source_table = ?
                AND a.source_id = line_messages.id
                AND a.annotation_type = ?
                AND a.model_id = ?
                AND a.is_excluded = 0
          )
        ORDER BY id
        {limit_clause}
    """


def _note_selection_query(limit: int | None) -> str:
    limit_clause = "" if limit is None else "LIMIT ?"
    return f"""
        SELECT 'notes' AS source_table,
               id AS source_id,
               TRIM(COALESCE(title, '') || char(10) || COALESCE(body_text, '')) AS text
        FROM notes
        WHERE is_excluded = 0
          AND TRIM(COALESCE(title, '') || COALESCE(body_text, '')) != ''
          AND NOT EXISTS (
              SELECT 1
              FROM text_annotations a
              WHERE a.source_table = ?
                AND a.source_id = notes.id
                AND a.annotation_type = ?
                AND a.model_id = ?
                AND a.is_excluded = 0
          )
        ORDER BY id
        {limit_clause}
    """


def _validate_entity(value: object) -> dict[str, Any]:
    item = _require_object(value, "entities[]")
    _reject_extra_keys(item, {"text", "type", "confidence"}, "entities[]")
    result = {
        "text": _require_nonempty_string(item.get("text"), "entities[].text"),
        "type": _optional_string(item.get("type"), default="unknown"),
    }
    if "confidence" in item:
        result["confidence"] = _require_confidence(item.get("confidence"), "entities[].confidence")
    return result


def _validate_date(value: object) -> dict[str, Any]:
    item = _require_object(value, "dates[]")
    _reject_extra_keys(item, {"text", "normalized", "role"}, "dates[]")
    return {
        "text": _require_nonempty_string(item.get("text"), "dates[].text"),
        "normalized": _optional_string(item.get("normalized")),
        "role": _optional_string(item.get("role"), default="mentioned"),
    }


def _validate_action_item(value: object) -> dict[str, Any]:
    item = _require_object(value, "action_items[]")
    _reject_extra_keys(item, {"text", "due_date", "assignee", "confidence"}, "action_items[]")
    result = {
        "text": _require_nonempty_string(item.get("text"), "action_items[].text"),
        "due_date": _optional_string(item.get("due_date")),
        "assignee": _optional_string(item.get("assignee")),
    }
    if "confidence" in item:
        result["confidence"] = _require_confidence(
            item.get("confidence"),
            "action_items[].confidence",
        )
    return result


def _validate_event_hint(value: object) -> dict[str, Any]:
    item = _require_object(value, "event_hints[]")
    _reject_extra_keys(item, {"title", "date_text", "confidence"}, "event_hints[]")
    result = {
        "title": _require_nonempty_string(item.get("title"), "event_hints[].title"),
        "date_text": _optional_string(item.get("date_text")),
    }
    if "confidence" in item:
        result["confidence"] = _require_confidence(
            item.get("confidence"),
            "event_hints[].confidence",
        )
    return result


def _require_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise TextExtractionError(f"{key} must be a list")
    return value


def _require_object(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TextExtractionError(f"{field_name} must be an object")
    return value


def _reject_extra_keys(item: dict[str, Any], allowed: set[str], field_name: str) -> None:
    extra = set(item) - allowed
    if extra:
        raise TextExtractionError(f"{field_name} has unexpected keys: {sorted(extra)}")


def _require_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TextExtractionError(f"{field_name} must be a string")
    return value


def _require_nonempty_string(value: object, field_name: str) -> str:
    text = _require_string(value, field_name).strip()
    if not text:
        raise TextExtractionError(f"{field_name} must not be empty")
    return text


def _optional_string(value: object, *, default: str | None = None) -> str | None:
    if value is None:
        return default
    if not isinstance(value, str):
        raise TextExtractionError("optional string field must be a string or null")
    stripped = value.strip()
    return stripped or default


def _require_confidence(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TextExtractionError(f"{field_name} must be a number between 0 and 1")
    confidence = float(value)
    if confidence < 0.0 or confidence > 1.0:
        raise TextExtractionError(f"{field_name} must be between 0 and 1")
    return confidence


def _normalize_source(source: str) -> str:
    source_key = source.strip().lower()
    if source_key not in SUPPORTED_TEXT_SOURCES:
        raise ValueError("source must be line or notes")
    return source_key


def _batched(items: list[dict[str, Any]], batch_size: int):
    for start in range(0, len(items), batch_size):
        yield items[start : start + batch_size]
