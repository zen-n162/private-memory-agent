"""Read-only photo and media metadata ingestion."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from private_memory_agent.storage import Storage, initialize_database

SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
SUPPORTED_VIDEO_EXTENSIONS = {".mov", ".mp4"}
SUPPORTED_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS | SUPPORTED_VIDEO_EXTENSIONS
SOURCE_TYPE = "photo"


@dataclass(frozen=True)
class PhotoMetadata:
    """Metadata extracted from a local source file."""

    path: Path
    sha256: str
    file_size_bytes: int
    modified_at: str
    media_type: str
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    taken_at: str | None = None
    gps: dict[str, Any] | None = None
    metadata_status: str = "ok"

    @property
    def metadata_json(self) -> str:
        metadata: dict[str, Any] = {
            "ingest_phase": "1-B",
            "metadata_status": self.metadata_status,
            "extension": self.path.suffix.lower(),
        }
        if self.gps:
            metadata["gps"] = self.gps
        if self.media_type == "video":
            metadata["video_metadata"] = "placeholder"
        return json.dumps(metadata, ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class PhotoIngestResult:
    """Summary-only ingest result safe for CLI output."""

    scanned: int = 0
    imported: int = 0
    skipped_duplicates: int = 0
    skipped_unsupported: int = 0
    errors: int = 0
    dry_run: bool = False


def ingest_photos(
    root: Path | str,
    *,
    db_path: Path | str | None = None,
    dry_run: bool = False,
) -> PhotoIngestResult:
    """Scan a folder and optionally store local media metadata.

    The source tree is read-only from this function's perspective. No source
    files are modified, renamed, moved, copied, or deleted.
    """

    root_path = Path(root).expanduser()
    if not root_path.is_dir():
        raise ValueError("photo ingest path must be an existing directory")

    storage: Storage | None = None
    if not dry_run:
        resolved_db_path = Path(db_path or "data/local/private_memory_agent.sqlite3").expanduser()
        storage = initialize_database(resolved_db_path)

    try:
        return _ingest_with_storage(root_path, storage=storage, dry_run=dry_run)
    finally:
        if storage is not None:
            storage.close()


def _ingest_with_storage(
    root_path: Path,
    *,
    storage: Storage | None,
    dry_run: bool,
) -> PhotoIngestResult:
    scanned = 0
    imported = 0
    skipped_duplicates = 0
    skipped_unsupported = 0
    errors = 0

    for path in _iter_source_files(root_path):
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            skipped_unsupported += 1
            continue
        scanned += 1
        try:
            metadata = extract_photo_metadata(path)
        except OSError:
            errors += 1
            continue

        if dry_run:
            imported += 1
            continue

        assert storage is not None
        if _is_duplicate(storage, metadata):
            skipped_duplicates += 1
            continue

        _store_metadata(storage, metadata)
        imported += 1

    return PhotoIngestResult(
        scanned=scanned,
        imported=imported,
        skipped_duplicates=skipped_duplicates,
        skipped_unsupported=skipped_unsupported,
        errors=errors,
        dry_run=dry_run,
    )


def extract_photo_metadata(path: Path) -> PhotoMetadata:
    stat = path.stat()
    sha256 = compute_sha256(path)
    modified_at = _timestamp_from_epoch(stat.st_mtime)
    extension = path.suffix.lower()
    media_type = "video" if extension in SUPPORTED_VIDEO_EXTENSIONS else "image"
    mime_type = mimetypes.guess_type(path.name)[0]

    width: int | None = None
    height: int | None = None
    taken_at: str | None = None
    gps: dict[str, Any] | None = None
    metadata_status = "ok"

    if media_type == "image":
        pillow_metadata = _extract_with_pillow(path)
        width = pillow_metadata.get("width")
        height = pillow_metadata.get("height")
        taken_at = pillow_metadata.get("taken_at")
        gps = pillow_metadata.get("gps")
        if width is None or height is None:
            width, height = _extract_dimensions_without_pillow(path, extension)
        if width is None or height is None:
            metadata_status = "partial"
    else:
        metadata_status = "placeholder"

    return PhotoMetadata(
        path=path.resolve(),
        sha256=sha256,
        file_size_bytes=stat.st_size,
        modified_at=modified_at,
        media_type=media_type,
        mime_type=mime_type,
        width=width,
        height=height,
        taken_at=taken_at,
        gps=gps,
        metadata_status=metadata_status,
    )


def compute_sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_source_files(root_path: Path):
    for current_root, dirnames, filenames in os.walk(root_path, followlinks=False):
        dirnames.sort()
        for filename in sorted(filenames):
            yield Path(current_root) / filename


def _is_duplicate(storage: Storage, metadata: PhotoMetadata) -> bool:
    source_uri = str(metadata.path)
    if storage.source_items.get_by_source_uri(source_type=SOURCE_TYPE, source_uri=source_uri):
        return True
    if storage.source_items.get_by_sha256(source_type=SOURCE_TYPE, content_sha256=metadata.sha256):
        return True
    return storage.media_items.get_by_sha256(metadata.sha256) is not None


def _store_metadata(storage: Storage, metadata: PhotoMetadata) -> None:
    with storage.transaction():
        source_id = storage.source_items.insert_source(
            source_type=SOURCE_TYPE,
            source_uri=str(metadata.path),
            content_sha256=metadata.sha256,
            metadata_json=json.dumps(
                {
                    "ingest_phase": "1-B",
                    "source_kind": metadata.media_type,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        storage.media_items.insert_media(
            source_item_id=source_id,
            media_type=metadata.media_type,
            file_path=str(metadata.path),
            sha256=metadata.sha256,
            mime_type=metadata.mime_type,
            file_size_bytes=metadata.file_size_bytes,
            width=metadata.width,
            height=metadata.height,
            taken_at=metadata.taken_at,
            modified_at=metadata.modified_at,
            metadata_json=metadata.metadata_json,
        )


def _extract_dimensions_without_pillow(path: Path, extension: str) -> tuple[int | None, int | None]:
    if extension == ".png":
        return _extract_png_dimensions(path)
    if extension in {".jpg", ".jpeg"}:
        return _extract_jpeg_dimensions(path)
    return None, None


def _extract_png_dimensions(path: Path) -> tuple[int | None, int | None]:
    with path.open("rb") as file:
        header = file.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        return None, None
    width, height = struct.unpack(">II", header[16:24])
    return int(width), int(height)


def _extract_jpeg_dimensions(path: Path) -> tuple[int | None, int | None]:
    with path.open("rb") as file:
        if file.read(2) != b"\xff\xd8":
            return None, None
        while True:
            marker_start = file.read(1)
            if not marker_start:
                return None, None
            if marker_start != b"\xff":
                continue
            marker = file.read(1)
            while marker == b"\xff":
                marker = file.read(1)
            if not marker:
                return None, None
            marker_value = marker[0]
            if marker_value in {0xD8, 0xD9} or 0xD0 <= marker_value <= 0xD7:
                continue
            segment_length_bytes = file.read(2)
            if len(segment_length_bytes) != 2:
                return None, None
            segment_length = struct.unpack(">H", segment_length_bytes)[0]
            if segment_length < 2:
                return None, None
            if marker_value in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            }:
                data = file.read(5)
                if len(data) != 5:
                    return None, None
                height, width = struct.unpack(">HH", data[1:5])
                return int(width), int(height)
            file.seek(segment_length - 2, os.SEEK_CUR)


def _extract_with_pillow(path: Path) -> dict[str, Any]:
    try:
        from PIL import ExifTags, Image
    except ImportError:
        return {}

    try:
        with Image.open(path) as image:
            result: dict[str, Any] = {"width": image.width, "height": image.height}
            exif = image.getexif()
            if not exif:
                return result
            tag_names = {value: key for key, value in ExifTags.TAGS.items()}
            gps_tag = tag_names.get("GPSInfo")
            for name in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
                tag_id = tag_names.get(name)
                if tag_id is not None and tag_id in exif:
                    result["taken_at"] = _normalize_exif_datetime(str(exif.get(tag_id)))
                    break
            if gps_tag is not None and gps_tag in exif:
                gps_raw = exif.get(gps_tag)
                result["gps"] = _json_safe_gps(gps_raw, ExifTags)
            return result
    except Exception:
        return {}


def _normalize_exif_datetime(value: str) -> str | None:
    if not value:
        return None
    # EXIF usually stores timestamps as YYYY:MM:DD HH:MM:SS.
    if len(value) >= 19 and value[4] == ":" and value[7] == ":":
        return f"{value[:4]}-{value[5:7]}-{value[8:]}"
    return value


def _json_safe_gps(gps_raw: Any, exif_tags: Any) -> dict[str, Any] | None:
    if not gps_raw:
        return None
    gps_tags = getattr(exif_tags, "GPSTAGS", {})
    safe: dict[str, Any] = {}
    try:
        items = gps_raw.items()
    except AttributeError:
        return {"raw": str(gps_raw)}
    for tag_id, value in items:
        tag_name = gps_tags.get(tag_id, str(tag_id))
        safe[str(tag_name)] = _json_safe_value(value)
    return safe


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item) for item in value]
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        denominator = getattr(value, "denominator")
        if denominator:
            return float(value)
    return str(value)


def _timestamp_from_epoch(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
