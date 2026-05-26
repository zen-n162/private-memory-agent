"""Privacy-safe media timestamp audit and backfill."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from xml.etree import ElementTree

from private_memory_agent.storage import initialize_database
from private_memory_agent.storage.repositories import utc_now

TimestampMethod = Literal["auto", "exiftool", "pillow"]
TimestampFallback = Literal["none", "file-mtime"]
TimestampConfidence = Literal["high", "medium", "low"]

SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
SUPPORTED_VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v"}
SUPPORTED_MEDIA_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS | SUPPORTED_VIDEO_EXTENSIONS
CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True)
class TimestampExtraction:
    """A single safe timestamp extraction result."""

    taken_at: str | None = None
    source: str | None = None
    confidence: TimestampConfidence | None = None
    timezone: str | None = None
    timezone_unknown: bool = True
    method: str | None = None
    error_class: str | None = None
    safe_message: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.taken_at is not None and self.source is not None and self.confidence is not None


@dataclass(frozen=True)
class TimestampAuditReport:
    """Privacy-safe media timestamp audit counts."""

    total_media_items: int
    taken_at_present_count: int
    taken_at_missing_count: int
    files_existing_count: int
    files_missing_count: int
    extractable_exif_datetime_count: int
    extractable_xmp_datetime_count: int
    extractable_video_datetime_count: int
    extractable_filename_datetime_count: int
    fallback_file_mtime_count: int
    unsupported_format_count: int
    parse_error_count: int
    extraction_checked_count: int = 0
    extraction_limit: int | None = None
    extraction_limited: bool = False
    month_histogram: dict[str, int] = field(default_factory=dict)
    method: str = "auto"
    fallback: str = "none"
    exiftool_available: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_media_items": self.total_media_items,
            "taken_at_present_count": self.taken_at_present_count,
            "taken_at_missing_count": self.taken_at_missing_count,
            "files_existing_count": self.files_existing_count,
            "files_missing_count": self.files_missing_count,
            "extractable_exif_datetime_count": self.extractable_exif_datetime_count,
            "extractable_xmp_datetime_count": self.extractable_xmp_datetime_count,
            "extractable_video_datetime_count": self.extractable_video_datetime_count,
            "extractable_filename_datetime_count": self.extractable_filename_datetime_count,
            "fallback_file_mtime_count": self.fallback_file_mtime_count,
            "unsupported_format_count": self.unsupported_format_count,
            "parse_error_count": self.parse_error_count,
            "extraction_checked_count": self.extraction_checked_count,
            "extraction_limit": self.extraction_limit,
            "extraction_limited": self.extraction_limited,
            "month_histogram": dict(self.month_histogram),
            "method": self.method,
            "fallback": self.fallback,
            "exiftool_available": self.exiftool_available,
        }


@dataclass(frozen=True)
class TimestampBackfillReport:
    """Privacy-safe timestamp backfill report."""

    total_selected_count: int
    processed_count: int
    updated_count: int
    dry_run_update_count: int
    skipped_existing_count: int
    files_missing_count: int
    unsupported_format_count: int
    parse_error_count: int
    fallback_file_mtime_count: int
    dry_run: bool
    method: str
    fallback: str
    min_confidence: str
    commit_interval: int
    commit_count: int
    error_classes: dict[str, int] = field(default_factory=dict)
    examples: tuple[dict[str, Any], ...] = ()

    def to_dict(self, *, show_errors: bool = False) -> dict[str, Any]:
        payload = {
            "mode_message": timestamp_backfill_mode_message(self),
            "total_selected_count": self.total_selected_count,
            "processed_count": self.processed_count,
            "updated_count": self.updated_count,
            "dry_run_update_count": self.dry_run_update_count,
            "skipped_existing_count": self.skipped_existing_count,
            "files_missing_count": self.files_missing_count,
            "unsupported_format_count": self.unsupported_format_count,
            "parse_error_count": self.parse_error_count,
            "fallback_file_mtime_count": self.fallback_file_mtime_count,
            "dry_run": self.dry_run,
            "method": self.method,
            "fallback": self.fallback,
            "min_confidence": self.min_confidence,
            "commit_interval": self.commit_interval,
            "commit_count": self.commit_count,
            "error_classes": dict(self.error_classes),
        }
        if show_errors:
            payload["examples"] = list(self.examples)
        return payload


def audit_media_timestamps(
    db_path: Path | str,
    *,
    method: TimestampMethod = "auto",
    fallback: TimestampFallback = "none",
    month_histogram: bool = False,
    extract_limit: int | None = None,
) -> TimestampAuditReport:
    """Audit media timestamp coverage without printing private paths."""

    storage = initialize_database(db_path)
    try:
        rows = storage.connection.execute(
            """
            SELECT id, file_path, media_type, taken_at
            FROM media_items
            WHERE is_excluded = 0
            ORDER BY id
            """,
        ).fetchall()
        total = len(rows)
        present = sum(1 for row in rows if _has_value(row["taken_at"]))
        files_existing = 0
        files_missing = 0
        exif_count = 0
        xmp_count = 0
        video_count = 0
        filename_count = 0
        mtime_count = 0
        unsupported_count = 0
        parse_error_count = 0
        extraction_checked = 0
        extraction_limited = False
        for row in rows:
            path = _path_from_row(row)
            if path is None or not path.exists():
                files_missing += 1
                continue
            files_existing += 1
            if not _is_supported(path):
                unsupported_count += 1
                continue
            if extract_limit is not None and extraction_checked >= extract_limit:
                extraction_limited = True
                continue
            extraction_checked += 1
            extraction = extract_media_timestamp(path, method=method, fallback=fallback)
            if extraction.succeeded:
                if extraction.source in {"exif_datetime_original", "exif_create_date", "exif_datetime_digitized"}:
                    exif_count += 1
                elif extraction.source == "xmp_datetime":
                    xmp_count += 1
                elif extraction.source in {"video_media_create_date", "video_track_create_date"}:
                    video_count += 1
                elif extraction.source == "filename_datetime":
                    filename_count += 1
                elif extraction.source == "file_mtime":
                    mtime_count += 1
            elif extraction.error_class == "UnsupportedFormat":
                unsupported_count += 1
            else:
                parse_error_count += 1
        histogram = _month_histogram(storage.connection) if month_histogram else {}
        return TimestampAuditReport(
            total_media_items=total,
            taken_at_present_count=present,
            taken_at_missing_count=total - present,
            files_existing_count=files_existing,
            files_missing_count=files_missing,
            extractable_exif_datetime_count=exif_count,
            extractable_xmp_datetime_count=xmp_count,
            extractable_video_datetime_count=video_count,
            extractable_filename_datetime_count=filename_count,
            fallback_file_mtime_count=mtime_count,
            unsupported_format_count=unsupported_count,
            parse_error_count=parse_error_count,
            extraction_checked_count=extraction_checked,
            extraction_limit=extract_limit,
            extraction_limited=extraction_limited,
            month_histogram=histogram,
            method=method,
            fallback=fallback,
            exiftool_available=shutil.which("exiftool") is not None,
        )
    finally:
        storage.close()


def backfill_media_timestamps(
    db_path: Path | str,
    *,
    dry_run: bool = True,
    limit: int | None = None,
    source: str = "photos",
    method: TimestampMethod = "auto",
    fallback: TimestampFallback = "none",
    min_confidence: TimestampConfidence = "high",
    only_missing: bool = True,
    show_errors: bool = False,
    commit_interval: int = 100,
) -> TimestampBackfillReport:
    """Backfill `media_items.taken_at` from local files without modifying sources."""

    if source != "photos":
        raise ValueError("only source=photos is supported for media timestamp backfill")
    if commit_interval <= 0:
        raise ValueError("commit_interval must be positive")
    storage = initialize_database(db_path)
    try:
        rows = _select_backfill_rows(storage.connection, only_missing=only_missing, limit=limit)
        processed = 0
        updated = 0
        dry_run_updates = 0
        skipped_existing = 0
        files_missing = 0
        unsupported = 0
        parse_errors = 0
        mtime_count = 0
        commit_count = 0
        updates_since_commit = 0
        error_classes: dict[str, int] = {}
        examples: list[dict[str, Any]] = []
        for row in rows:
            if only_missing and _has_value(row["taken_at"]):
                skipped_existing += 1
                continue
            processed += 1
            path = _path_from_row(row)
            if path is None or not path.exists():
                files_missing += 1
                _record_error(
                    error_classes,
                    examples,
                    media_item_id=int(row["id"]),
                    error_class="FileMissing",
                    message="media source file is missing",
                    show_errors=show_errors,
                )
                continue
            if not _is_supported(path):
                unsupported += 1
                _record_error(
                    error_classes,
                    examples,
                    media_item_id=int(row["id"]),
                    error_class="UnsupportedFormat",
                    message="media format is unsupported for timestamp extraction",
                    show_errors=show_errors,
                )
                continue
            extraction = extract_media_timestamp(path, method=method, fallback=fallback)
            if not extraction.succeeded or extraction.confidence is None:
                parse_errors += 1
                _record_error(
                    error_classes,
                    examples,
                    media_item_id=int(row["id"]),
                    error_class=extraction.error_class or "TimestampParseError",
                    message=extraction.safe_message or "timestamp could not be extracted",
                    show_errors=show_errors,
                )
                continue
            if not _confidence_at_least(extraction.confidence, min_confidence):
                parse_errors += 1
                _record_error(
                    error_classes,
                    examples,
                    media_item_id=int(row["id"]),
                    error_class="ConfidenceTooLow",
                    message="extracted timestamp confidence is below the requested threshold",
                    show_errors=show_errors,
                )
                continue
            if extraction.source == "file_mtime":
                mtime_count += 1
            if dry_run:
                dry_run_updates += 1
                continue
            row_updates = _update_media_timestamp(
                storage.connection,
                media_item_id=int(row["id"]),
                extraction=extraction,
            )
            updated += row_updates
            updates_since_commit += row_updates
            if updates_since_commit >= commit_interval:
                storage.connection.commit()
                commit_count += 1
                updates_since_commit = 0
        if not dry_run and updates_since_commit > 0:
            storage.connection.commit()
            commit_count += 1
        return TimestampBackfillReport(
            total_selected_count=len(rows),
            processed_count=processed,
            updated_count=updated,
            dry_run_update_count=dry_run_updates,
            skipped_existing_count=skipped_existing,
            files_missing_count=files_missing,
            unsupported_format_count=unsupported,
            parse_error_count=parse_errors,
            fallback_file_mtime_count=mtime_count,
            dry_run=dry_run,
            method=method,
            fallback=fallback,
            min_confidence=min_confidence,
            commit_interval=commit_interval,
            commit_count=commit_count,
            error_classes=error_classes,
            examples=tuple(examples),
        )
    finally:
        storage.close()


def extract_media_timestamp(
    path: Path | str,
    *,
    method: TimestampMethod = "auto",
    fallback: TimestampFallback = "none",
) -> TimestampExtraction:
    """Extract one timestamp from a local media path."""

    media_path = Path(path)
    if not _is_supported(media_path):
        return TimestampExtraction(error_class="UnsupportedFormat", safe_message="unsupported media format")
    if method not in {"auto", "exiftool", "pillow"}:
        raise ValueError("method must be auto, exiftool, or pillow")
    extraction: TimestampExtraction | None = None
    if method in {"auto", "exiftool"} and shutil.which("exiftool") is not None:
        extraction = _extract_with_exiftool(media_path)
        if extraction.succeeded:
            return extraction
    elif method == "exiftool":
        return TimestampExtraction(error_class="ExiftoolUnavailable", safe_message="exiftool is unavailable")

    if method in {"auto", "pillow"} and media_path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
        extraction = _extract_with_pillow(media_path)
        if extraction.succeeded:
            return extraction
    elif method == "pillow" and media_path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS:
        return TimestampExtraction(error_class="UnsupportedFormat", safe_message="Pillow does not support video metadata")

    xmp = _extract_with_xmp_sidecar(media_path)
    if xmp.succeeded:
        return xmp
    filename = _extract_from_filename(media_path)
    if filename.succeeded:
        return filename
    if fallback == "file-mtime":
        return _extract_file_mtime(media_path)
    return extraction or TimestampExtraction(
        error_class="TimestampNotFound",
        safe_message="no supported timestamp tag was found",
    )


def format_timestamp_audit(report: TimestampAuditReport) -> str:
    payload = report.to_dict()
    lines = ["Media timestamp audit:"]
    for key, value in payload.items():
        if key == "month_histogram":
            continue
        lines.append(f"{key}={value}")
    if report.month_histogram:
        lines.append("month_histogram:")
        for month, count in sorted(report.month_histogram.items()):
            lines.append(f"  {month}: {count}")
    return "\n".join(lines)


def format_timestamp_backfill(report: TimestampBackfillReport, *, show_errors: bool = False) -> str:
    payload = report.to_dict(show_errors=show_errors)
    lines = ["Media timestamp backfill:", timestamp_backfill_mode_message(report)]
    for key, value in payload.items():
        if key in {"examples", "mode_message"}:
            continue
        lines.append(f"{key}={value}")
    if show_errors and report.examples:
        lines.append("examples:")
        for example in report.examples:
            lines.append(
                "  "
                + "; ".join(
                    [
                        f"media_item_id={example['media_item_id']}",
                        f"class={example['class']}",
                        f"message={example['message']}",
                    ],
                ),
            )
    return "\n".join(lines)


def timestamp_backfill_mode_message(report: TimestampBackfillReport) -> str:
    """Return a privacy-safe explanation of dry-run/apply behavior."""

    if report.dry_run:
        return "DRY RUN: no database rows were updated. Re-run with --apply to write timestamps."
    return "APPLY MODE: database metadata was updated. Source files were not modified."


def timestamp_coverage(db_path: Path | str) -> dict[str, Any]:
    """Return cheap timestamp coverage for temporal diagnostics."""

    storage = initialize_database(db_path)
    try:
        row = storage.connection.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN taken_at IS NOT NULL AND taken_at != '' THEN 1 ELSE 0 END) AS present
            FROM media_items
            WHERE is_excluded = 0
            """,
        ).fetchone()
        total = int(row["total"] or 0)
        present = int(row["present"] or 0)
        missing = total - present
        missing_ratio = (missing / total) if total else 0.0
        return {
            "media_items_total_count": total,
            "media_items_with_taken_at_count": present,
            "media_items_missing_taken_at_count": missing,
            "timestamp_backfill_recommended": total > 0 and missing_ratio >= 0.5,
        }
    finally:
        storage.close()


def _select_backfill_rows(connection: Any, *, only_missing: bool, limit: int | None) -> list[Any]:
    where = "WHERE is_excluded = 0"
    if only_missing:
        where += " AND (taken_at IS NULL OR taken_at = '')"
    sql = f"""
        SELECT id, file_path, media_type, taken_at, taken_at_confidence
        FROM media_items
        {where}
        ORDER BY id
    """
    params: tuple[Any, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (int(limit),)
    return list(connection.execute(sql, params).fetchall())


def _update_media_timestamp(connection: Any, *, media_item_id: int, extraction: TimestampExtraction) -> int:
    cursor = connection.execute(
        """
        UPDATE media_items
        SET taken_at = ?,
            taken_at_source = ?,
            taken_at_confidence = ?,
            taken_at_timezone = ?,
            taken_at_timezone_unknown = ?,
            metadata_updated_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            extraction.taken_at,
            extraction.source,
            extraction.confidence,
            extraction.timezone,
            1 if extraction.timezone_unknown else 0,
            utc_now(),
            utc_now(),
            media_item_id,
        ),
    )
    return int(cursor.rowcount or 0)


def _extract_with_exiftool(path: Path) -> TimestampExtraction:
    try:
        completed = subprocess.run(
            [
                "exiftool",
                "-json",
                "-DateTimeOriginal",
                "-CreateDate",
                "-DateTimeDigitized",
                "-MediaCreateDate",
                "-TrackCreateDate",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return TimestampExtraction(error_class="ExiftoolError", safe_message="exiftool request failed")
    if completed.returncode != 0:
        return TimestampExtraction(error_class="ExiftoolError", safe_message="exiftool could not read timestamp metadata")
    try:
        payload = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError:
        return TimestampExtraction(error_class="TimestampParseError", safe_message="exiftool JSON could not be parsed")
    if not payload:
        return TimestampExtraction(error_class="TimestampNotFound", safe_message="no exiftool metadata returned")
    row = payload[0]
    tag_sources = (
        ("DateTimeOriginal", "exif_datetime_original"),
        ("CreateDate", "exif_create_date"),
        ("DateTimeDigitized", "exif_datetime_digitized"),
        ("MediaCreateDate", "video_media_create_date"),
        ("TrackCreateDate", "video_track_create_date"),
    )
    for tag, source in tag_sources:
        normalized = _normalize_timestamp(row.get(tag))
        if normalized:
            return TimestampExtraction(
                taken_at=normalized,
                source=source,
                confidence="high",
                timezone=_timezone_from_text(str(row.get(tag) or "")),
                timezone_unknown=_timezone_from_text(str(row.get(tag) or "")) is None,
                method="exiftool",
            )
    return TimestampExtraction(error_class="TimestampNotFound", safe_message="no supported exiftool timestamp tag found")


def _extract_with_pillow(path: Path) -> TimestampExtraction:
    try:
        from PIL import Image
    except ImportError:
        return TimestampExtraction(error_class="PillowUnavailable", safe_message="Pillow is unavailable")
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            if not exif:
                return TimestampExtraction(error_class="TimestampNotFound", safe_message="no EXIF timestamp tags found")
            for tag, source in (
                (36867, "exif_datetime_original"),
                (36868, "exif_datetime_digitized"),
                (306, "exif_create_date"),
            ):
                normalized = _normalize_timestamp(exif.get(tag))
                if normalized:
                    return TimestampExtraction(
                        taken_at=normalized,
                        source=source,
                        confidence="high",
                        method="pillow",
                        timezone_unknown=True,
                    )
    except OSError:
        return TimestampExtraction(error_class="PillowReadError", safe_message="Pillow could not read media metadata")
    return TimestampExtraction(error_class="TimestampNotFound", safe_message="no EXIF timestamp tags found")


def _extract_with_xmp_sidecar(path: Path) -> TimestampExtraction:
    candidates = (path.with_suffix(".xmp"), Path(str(path) + ".xmp"))
    for sidecar in candidates:
        if not sidecar.exists():
            continue
        try:
            text = sidecar.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        timestamp = _timestamp_from_xmp_text(text)
        if timestamp:
            return TimestampExtraction(
                taken_at=timestamp,
                source="xmp_datetime",
                confidence="high",
                method="xmp_sidecar",
                timezone=_timezone_from_text(text),
                timezone_unknown=_timezone_from_text(text) is None,
            )
    return TimestampExtraction(error_class="TimestampNotFound", safe_message="no XMP sidecar timestamp found")


def _timestamp_from_xmp_text(text: str) -> str | None:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        root = None
    if root is not None:
        for element in root.iter():
            local_name = element.tag.rsplit("}", 1)[-1].lower()
            if local_name in {"datetimeoriginal", "createdate", "modifydate"}:
                normalized = _normalize_timestamp(element.text)
                if normalized:
                    return normalized
    match = re.search(
        r"(?:DateTimeOriginal|CreateDate|ModifyDate)[^0-9]*(\d{4}[-:]\d{2}[-:]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:?\d{2})?)",
        text,
    )
    return _normalize_timestamp(match.group(1)) if match else None


def _extract_from_filename(path: Path) -> TimestampExtraction:
    text = str(path.name)
    patterns = (
        r"(?P<year>20\d{2})(?P<month>\d{2})(?P<day>\d{2})[_-]?(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})",
        r"(?P<year>20\d{2})[-_](?P<month>\d{2})[-_](?P<day>\d{2})[ T_-](?P<hour>\d{2})[-_:]?(?P<minute>\d{2})[-_:]?(?P<second>\d{2})",
        r"(?P<year>20\d{2})[-_](?P<month>\d{2})[-_](?P<day>\d{2})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        parts = match.groupdict(default="00")
        normalized = _normalize_timestamp(
            "{year}-{month}-{day}T{hour}:{minute}:{second}".format(**parts),
        )
        if normalized:
            return TimestampExtraction(
                taken_at=normalized,
                source="filename_datetime",
                confidence="medium",
                method="filename",
                timezone_unknown=True,
            )
    return TimestampExtraction(error_class="TimestampNotFound", safe_message="no clear filename timestamp found")


def _extract_file_mtime(path: Path) -> TimestampExtraction:
    try:
        timestamp = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return TimestampExtraction(error_class="FileMissing", safe_message="media source file is missing")
    return TimestampExtraction(
        taken_at=timestamp.isoformat(timespec="seconds").replace("+00:00", "Z"),
        source="file_mtime",
        confidence="low",
        timezone="UTC",
        timezone_unknown=False,
        method="file_mtime",
    )


def _normalize_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.startswith("0000"):
        return None
    cleaned = re.sub(r"^(\d{4}):(\d{2}):(\d{2})", r"\1-\2-\3", text)
    cleaned = cleaned.replace(" ", "T", 1)
    cleaned = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", cleaned)
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.isoformat(timespec="seconds")
    return parsed.isoformat(timespec="seconds")


def _timezone_from_text(value: str) -> str | None:
    match = re.search(r"(Z|[+-]\d{2}:?\d{2})\s*$", value.strip())
    if not match:
        return None
    timezone = match.group(1)
    if timezone == "Z":
        return "UTC"
    if len(timezone) == 5 and ":" not in timezone:
        timezone = f"{timezone[:3]}:{timezone[3:]}"
    return timezone


def _record_error(
    error_classes: dict[str, int],
    examples: list[dict[str, Any]],
    *,
    media_item_id: int,
    error_class: str,
    message: str,
    show_errors: bool,
) -> None:
    error_classes[error_class] = error_classes.get(error_class, 0) + 1
    if show_errors and len(examples) < 5:
        examples.append(
            {
                "media_item_id": media_item_id,
                "class": error_class,
                "message": message[:160],
            },
        )


def _path_from_row(row: Any) -> Path | None:
    value = row["file_path"]
    if value is None or str(value).strip() == "":
        return None
    return Path(str(value)).expanduser()


def _is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_MEDIA_EXTENSIONS


def _has_value(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _confidence_at_least(value: TimestampConfidence, minimum: TimestampConfidence) -> bool:
    return CONFIDENCE_RANK[value] >= CONFIDENCE_RANK[minimum]


def _month_histogram(connection: Any) -> dict[str, int]:
    rows = connection.execute(
        """
        SELECT substr(taken_at, 1, 7) AS month, COUNT(*) AS count
        FROM media_items
        WHERE is_excluded = 0
          AND taken_at IS NOT NULL
          AND taken_at != ''
        GROUP BY substr(taken_at, 1, 7)
        ORDER BY month
        """,
    ).fetchall()
    return {str(row["month"]): int(row["count"]) for row in rows if row["month"]}
