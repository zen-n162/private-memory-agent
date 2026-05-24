"""Privacy-safe annotation progress and failure statistics."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from private_memory_agent.storage import initialize_database

PHOTO_ANNOTATION_ERROR_ACTION = "photo_annotation.error"


@dataclass(frozen=True)
class ModelAnnotationCount:
    """Annotation count for one model id."""

    model_id: str
    count: int

    def to_dict(self) -> dict[str, Any]:
        return {"model_id": self.model_id, "count": self.count}


@dataclass(frozen=True)
class PhotoAnnotationStats:
    """Aggregate photo annotation status safe for CLI output."""

    media_items_count: int
    image_media_items_count: int
    media_annotations_count: int
    annotated_media_count: int
    unannotated_media_count: int
    failed_annotation_count: int
    failed_annotation_event_count: int
    skipped_unsupported_format_count: int
    annotation_success_rate: float
    model_id_breakdown: tuple[ModelAnnotationCount, ...] = ()
    latest_annotation_timestamp: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "media_items_count": self.media_items_count,
            "image_media_items_count": self.image_media_items_count,
            "media_annotations_count": self.media_annotations_count,
            "annotated_media_count": self.annotated_media_count,
            "unannotated_media_count": self.unannotated_media_count,
            "failed_annotation_count": self.failed_annotation_count,
            "failed_annotation_event_count": self.failed_annotation_event_count,
            "skipped_unsupported_format_count": self.skipped_unsupported_format_count,
            "annotation_success_rate": self.annotation_success_rate,
            "model_id_breakdown": [item.to_dict() for item in self.model_id_breakdown],
            "latest_annotation_timestamp": self.latest_annotation_timestamp,
        }


@dataclass(frozen=True)
class FailedPhotoAnnotation:
    """One privacy-safe tracked photo annotation failure."""

    media_item_id: int
    error_class: str
    message: str
    created_at: str
    model_id: str | None = None
    image_format: str | None = None
    dimensions: str | None = None
    preprocessing_succeeded: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "media_item_id": self.media_item_id,
            "error_class": self.error_class,
            "message": self.message,
            "created_at": self.created_at,
            "model_id": self.model_id,
            "image_format": self.image_format,
            "dimensions": self.dimensions,
            "preprocessing_succeeded": self.preprocessing_succeeded,
        }


@dataclass(frozen=True)
class AnnotationStatsReport:
    """Top-level stats report safe for CLI output."""

    photo_annotations: PhotoAnnotationStats

    def to_dict(self) -> dict[str, Any]:
        return {"photo_annotations": self.photo_annotations.to_dict()}


@dataclass(frozen=True)
class FailedPhotoAnnotationReport:
    """Top-level failed annotation report safe for CLI output."""

    failed_annotations: tuple[FailedPhotoAnnotation, ...] = ()
    failed_annotation_count: int = 0
    privacy: dict[str, str] = field(
        default_factory=lambda: {
            "fields": "media item ids and sanitized error metadata only",
        },
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "failed_annotation_count": self.failed_annotation_count,
            "failed_annotations": [item.to_dict() for item in self.failed_annotations],
            "privacy": dict(self.privacy),
        }


def build_annotation_stats_report(db_path: Path | str) -> AnnotationStatsReport:
    """Return aggregate annotation status without reading private source files."""

    storage = initialize_database(db_path)
    try:
        connection = storage.connection
        media_items_count = _count_one(
            connection,
            "SELECT COUNT(*) AS count FROM media_items WHERE is_excluded = 0",
        )
        image_media_items_count = _count_one(
            connection,
            """
            SELECT COUNT(*) AS count
            FROM media_items
            WHERE is_excluded = 0
              AND media_type = 'image'
            """,
        )
        media_annotations_count = _count_one(
            connection,
            """
            SELECT COUNT(*) AS count
            FROM media_annotations
            WHERE is_excluded = 0
              AND annotation_type = 'vision'
            """,
        )
        annotated_media_count = _count_one(
            connection,
            """
            SELECT COUNT(DISTINCT media_item_id) AS count
            FROM media_annotations
            WHERE is_excluded = 0
              AND annotation_type = 'vision'
            """,
        )
        unannotated_media_count = _count_one(
            connection,
            """
            SELECT COUNT(*) AS count
            FROM media_items m
            WHERE m.is_excluded = 0
              AND m.media_type = 'image'
              AND NOT EXISTS (
                  SELECT 1
                  FROM media_annotations a
                  WHERE a.media_item_id = m.id
                    AND a.annotation_type = 'vision'
                    AND a.is_excluded = 0
              )
            """,
        )
        failed_annotation_count = _count_one(
            connection,
            """
            SELECT COUNT(DISTINCT target_id) AS count
            FROM audit_log
            WHERE is_excluded = 0
              AND action = ?
              AND status = 'error'
              AND target_table = 'media_items'
            """,
            (PHOTO_ANNOTATION_ERROR_ACTION,),
        )
        failed_annotation_event_count = _count_one(
            connection,
            """
            SELECT COUNT(*) AS count
            FROM audit_log
            WHERE is_excluded = 0
              AND action = ?
              AND status = 'error'
              AND target_table = 'media_items'
            """,
            (PHOTO_ANNOTATION_ERROR_ACTION,),
        )
        skipped_unsupported_format_count = _count_unsupported_image_formats(connection)
        latest_row = connection.execute(
            """
            SELECT MAX(created_at) AS latest
            FROM media_annotations
            WHERE is_excluded = 0
              AND annotation_type = 'vision'
            """,
        ).fetchone()
        model_rows = connection.execute(
            """
            SELECT COALESCE(NULLIF(model_id, ''), '<unknown>') AS model_id,
                   COUNT(*) AS count
            FROM media_annotations
            WHERE is_excluded = 0
              AND annotation_type = 'vision'
            GROUP BY COALESCE(NULLIF(model_id, ''), '<unknown>')
            ORDER BY count DESC, model_id
            """,
        ).fetchall()
        success_rate = (
            round(annotated_media_count / image_media_items_count, 4)
            if image_media_items_count
            else 0.0
        )
        return AnnotationStatsReport(
            photo_annotations=PhotoAnnotationStats(
                media_items_count=media_items_count,
                image_media_items_count=image_media_items_count,
                media_annotations_count=media_annotations_count,
                annotated_media_count=annotated_media_count,
                unannotated_media_count=unannotated_media_count,
                failed_annotation_count=failed_annotation_count,
                failed_annotation_event_count=failed_annotation_event_count,
                skipped_unsupported_format_count=skipped_unsupported_format_count,
                annotation_success_rate=success_rate,
                model_id_breakdown=tuple(
                    ModelAnnotationCount(
                        model_id=str(row["model_id"]),
                        count=int(row["count"]),
                    )
                    for row in model_rows
                ),
                latest_annotation_timestamp=latest_row["latest"] if latest_row else None,
            ),
        )
    finally:
        storage.close()


def list_failed_photo_annotations(
    db_path: Path | str,
    *,
    limit: int = 50,
) -> FailedPhotoAnnotationReport:
    """Return tracked photo annotation failures with safe IDs only."""

    storage = initialize_database(db_path)
    try:
        total = _count_one(
            storage.connection,
            """
            SELECT COUNT(*) AS count
            FROM audit_log
            WHERE is_excluded = 0
              AND action = ?
              AND status = 'error'
              AND target_table = 'media_items'
            """,
            (PHOTO_ANNOTATION_ERROR_ACTION,),
        )
        rows = storage.connection.execute(
            """
            SELECT target_id, detail_json, created_at
            FROM audit_log
            WHERE is_excluded = 0
              AND action = ?
              AND status = 'error'
              AND target_table = 'media_items'
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (PHOTO_ANNOTATION_ERROR_ACTION, max(0, limit)),
        ).fetchall()
        return FailedPhotoAnnotationReport(
            failed_annotations=tuple(_failed_annotation_from_row(row) for row in rows),
            failed_annotation_count=total,
        )
    finally:
        storage.close()


def _count_one(connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    row = connection.execute(sql, params).fetchone()
    return int(row["count"] if row is not None else 0)


def _count_unsupported_image_formats(connection) -> int:
    rows = connection.execute(
        """
        SELECT mime_type, file_path
        FROM media_items
        WHERE is_excluded = 0
          AND media_type = 'image'
        """,
    ).fetchall()
    return sum(1 for row in rows if not _is_supported_image_reference(row["mime_type"], row["file_path"]))


def _is_supported_image_reference(mime_type: object, file_path: object) -> bool:
    if isinstance(mime_type, str) and mime_type.casefold().strip() in {
        "image/jpeg",
        "image/png",
        "image/webp",
    }:
        return True
    if isinstance(file_path, str):
        suffix = Path(file_path).suffix.casefold()
        return suffix in {".jpg", ".jpeg", ".png", ".webp"}
    return False


def _failed_annotation_from_row(row) -> FailedPhotoAnnotation:
    detail = _safe_detail_json(row["detail_json"])
    return FailedPhotoAnnotation(
        media_item_id=int(row["target_id"]),
        error_class=str(detail.get("error_class") or "UnknownError")[:80],
        message=str(detail.get("message") or "annotation failed")[:160],
        created_at=str(row["created_at"]),
        model_id=_optional_string(detail.get("model_id")),
        image_format=_optional_string(detail.get("image_format")),
        dimensions=_optional_string(detail.get("dimensions")),
        preprocessing_succeeded=_optional_bool(detail.get("preprocessing_succeeded")),
    )


def _safe_detail_json(value: object) -> dict[str, Any]:
    if not isinstance(value, str):
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:120] if text else None


def _optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    return None
