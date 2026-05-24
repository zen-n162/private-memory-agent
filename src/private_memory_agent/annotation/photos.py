"""Photo annotation pipeline using pluggable local vision clients."""

from __future__ import annotations

import base64
import json
import traceback
from collections import Counter
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

from private_memory_agent.annotation.stats import PHOTO_ANNOTATION_ERROR_ACTION
from private_memory_agent.runtime import (
    VisionInput,
    VisionModelClient,
    VisionRequest,
    VisionResponse,
)
from private_memory_agent.storage import Storage, initialize_database

DEFAULT_PHOTO_ANNOTATION_PROMPT = (
    "Describe the image for a private local memory index. Return concise JSON if possible "
    "with caption, objects, ocr_text, and confidence. Do not identify faces."
)
ANNOTATION_TYPE = "vision"
ANNOTATION_SOURCE = "model"
_SUPPORTED_IMAGE_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_SUPPORTED_IMAGE_MIME_TYPES = set(_SUPPORTED_IMAGE_MIME_BY_SUFFIX.values())
DEFAULT_PREPROCESS_MAX_SIDE_PX = 1280
DEFAULT_PREPROCESS_OUTPUT_FORMAT = "jpeg"
DEFAULT_PREPROCESS_JPEG_QUALITY = 90


class UnsupportedImageFormat(ValueError):
    """Raised when a selected image format cannot be sent to the vision model."""


class ImagePreprocessingError(RuntimeError):
    """Raised when a supported image cannot be safely prepared for vision."""


@dataclass(frozen=True)
class PhotoPreprocessOptions:
    """Options for local read-only image preprocessing before vision requests."""

    max_side_px: int = DEFAULT_PREPROCESS_MAX_SIDE_PX
    output_format: str = DEFAULT_PREPROCESS_OUTPUT_FORMAT
    quality: int = DEFAULT_PREPROCESS_JPEG_QUALITY

    def normalized_format(self) -> str:
        normalized = self.output_format.casefold().strip()
        if normalized in {"jpg", "jpeg"}:
            return "jpeg"
        if normalized == "png":
            return "png"
        raise ValueError("image output format must be jpeg or png")

    def validate(self) -> None:
        if self.max_side_px <= 0:
            raise ValueError("max_side_px must be positive")
        if not 1 <= self.quality <= 100:
            raise ValueError("image quality must be between 1 and 100")
        self.normalized_format()


@dataclass(frozen=True)
class PreprocessedVisionImage:
    """Processed image bytes safe to send to a local vision endpoint."""

    data: bytes
    mime_type: str
    width: int
    height: int
    original_width: int
    original_height: int
    source_mime_type: str

    @property
    def base64_data(self) -> str:
        return base64.b64encode(self.data).decode("ascii")

    @property
    def data_uri(self) -> str:
        return f"data:{self.mime_type};base64,{self.base64_data}"


@dataclass(frozen=True)
class PhotoAnnotation:
    """Normalized vision annotation output."""

    caption: str | None
    objects: tuple[str, ...] = ()
    ocr_text: str | None = None
    confidence: float | None = None
    model_id: str = "unknown-vision-model"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PhotoAnnotationResult:
    """Summary-only result safe for CLI output."""

    selected: int = 0
    would_annotate: int = 0
    annotated: int = 0
    skipped_already_annotated: int = 0
    skipped_missing_file: int = 0
    preprocessed: int = 0
    preprocess_checked: bool = False
    errors: int = 0
    model_id: str = "unknown-vision-model"
    endpoint_url: str | None = None
    dry_run: bool = False
    error_details: tuple["PhotoAnnotationErrorDetail", ...] = ()

    def top_error_classes(self, *, limit: int = 5) -> list[tuple[str, int]]:
        counts = Counter(detail.error_class for detail in self.error_details)
        return counts.most_common(limit)


@dataclass(frozen=True)
class PhotoAnnotationErrorDetail:
    """Privacy-safe annotation error detail."""

    media_item_id: int
    error_class: str
    message: str
    stack_summary: str = ""
    image_format: str | None = None
    dimensions: str | None = None
    preprocessing_succeeded: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "media_item_id": self.media_item_id,
            "error_class": self.error_class,
            "message": self.message,
            "stack_summary": self.stack_summary,
            "image_format": self.image_format,
            "dimensions": self.dimensions,
            "preprocessing_succeeded": self.preprocessing_succeeded,
        }


def annotate_photos(
    db_path: Path | str,
    *,
    client: VisionModelClient,
    model_id: str,
    limit: int | None = None,
    batch_size: int = 4,
    prompt: str = DEFAULT_PHOTO_ANNOTATION_PROMPT,
    dry_run: bool = False,
    fail_fast: bool = False,
    endpoint_url: str | None = None,
    preprocess_options: PhotoPreprocessOptions | None = None,
    check_preprocess: bool = False,
) -> PhotoAnnotationResult:
    """Annotate unannotated image media rows.

    Source files are opened read-only and never modified. CLI callers should
    print only the returned summary, not model text or source paths.
    """

    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    options = preprocess_options or PhotoPreprocessOptions()
    options.validate()

    storage = initialize_database(db_path)
    selected = 0
    would_annotate = 0
    annotated = 0
    skipped_already_annotated = 0
    skipped_missing_file = 0
    preprocessed = 0
    errors = 0
    error_details: list[PhotoAnnotationErrorDetail] = []
    try:
        candidates = select_unannotated_photo_media(storage, limit=limit)
        selected = len(candidates)
        for batch in _batched(candidates, batch_size):
            for media_item in batch:
                media_item_id = int(media_item["id"])
                if _has_vision_annotation(storage, media_item_id):
                    skipped_already_annotated += 1
                    continue
                file_path = media_item.get("file_path")
                if not file_path or not Path(str(file_path)).is_file():
                    skipped_missing_file += 1
                    continue
                preprocess_context = _safe_media_context(media_item)
                try:
                    if dry_run:
                        if check_preprocess:
                            processed = preprocess_image_for_vision(
                                Path(str(file_path)),
                                media_item.get("mime_type"),
                                options=options,
                            )
                            preprocess_context = _context_from_preprocessed(processed)
                            preprocessed += 1
                        else:
                            _validate_media_file(media_item)
                        would_annotate += 1
                        continue
                    request, processed = _build_vision_request(
                        media_item,
                        prompt=prompt,
                        model_id=model_id,
                        preprocess_options=options,
                    )
                    preprocess_context = _context_from_preprocessed(processed)
                    preprocessed += 1
                    response = client.analyze(request)
                    annotation = normalize_photo_annotation(response, fallback_model_id=model_id)
                    _store_photo_annotation(storage, media_item_id, annotation)
                    annotated += 1
                except (OSError, ValueError, RuntimeError) as exc:
                    errors += 1
                    detail = _error_detail(media_item_id, exc, preprocess_context)
                    error_details.append(detail)
                    _record_annotation_error(storage, media_item_id, detail, model_id=model_id)
                    if fail_fast:
                        break
            if fail_fast and error_details:
                break
    finally:
        storage.close()

    return PhotoAnnotationResult(
        selected=selected,
        would_annotate=would_annotate,
        annotated=annotated,
        skipped_already_annotated=skipped_already_annotated,
        skipped_missing_file=skipped_missing_file,
        preprocessed=preprocessed,
        preprocess_checked=check_preprocess,
        errors=errors,
        model_id=model_id,
        endpoint_url=endpoint_url,
        dry_run=dry_run,
        error_details=tuple(error_details),
    )


def select_unannotated_photo_media(
    storage: Storage,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return image media rows without an active vision annotation."""

    limit_clause = "" if limit is None else "LIMIT ?"
    params: tuple[Any, ...] = () if limit is None else (limit,)
    rows = storage.connection.execute(
        f"""
        SELECT m.*
        FROM media_items m
        WHERE m.is_excluded = 0
          AND m.media_type = 'image'
          AND NOT EXISTS (
              SELECT 1
              FROM media_annotations a
              WHERE a.media_item_id = m.id
                AND a.annotation_type = ?
                AND a.is_excluded = 0
          )
        ORDER BY m.id
        {limit_clause}
        """,
        (ANNOTATION_TYPE, *params),
    ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def normalize_photo_annotation(
    response: VisionResponse,
    *,
    fallback_model_id: str,
) -> PhotoAnnotation:
    """Normalize a vision response into the annotation storage shape."""

    payload = _extract_annotation_payload(response)
    caption = _optional_text(payload.get("caption")) or _optional_text(response.text)
    objects = tuple(_string_list(payload.get("objects")))
    ocr_text = _optional_text(payload.get("ocr_text") or payload.get("ocr"))
    confidence = _optional_float(payload.get("confidence"))
    model_id = response.model or fallback_model_id
    return PhotoAnnotation(
        caption=caption,
        objects=objects,
        ocr_text=ocr_text,
        confidence=confidence,
        model_id=model_id,
        raw=_redacted_raw_payload(payload),
    )


def _build_vision_request(
    media_item: dict[str, Any],
    *,
    prompt: str,
    model_id: str,
    preprocess_options: PhotoPreprocessOptions,
) -> tuple[VisionRequest, PreprocessedVisionImage]:
    file_path = Path(str(media_item["file_path"]))
    processed = preprocess_image_for_vision(
        file_path,
        media_item.get("mime_type"),
        options=preprocess_options,
    )
    request = VisionRequest(
        prompt=prompt,
        images=(VisionInput(kind="base64", data=processed.base64_data, mime_type=processed.mime_type),),
        model=model_id,
        temperature=0.2,
        max_tokens=512,
    )
    return request, processed


def preprocess_image_for_vision(
    file_path: Path,
    configured_mime_type: object = None,
    *,
    options: PhotoPreprocessOptions | None = None,
) -> PreprocessedVisionImage:
    """Read, resize, strip metadata, and encode an image for local vision models."""

    preprocess_options = options or PhotoPreprocessOptions()
    preprocess_options.validate()
    source_mime_type = detect_image_mime(file_path, configured_mime_type)
    output_format = preprocess_options.normalized_format()
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:
        raise ImagePreprocessingError("Pillow is required for image preprocessing") from exc

    try:
        with Image.open(file_path) as image:
            original_width, original_height = image.size
            image.load()
            converted = image.convert("RGB")
        max_side = max(converted.width, converted.height)
        if max_side > preprocess_options.max_side_px:
            scale = preprocess_options.max_side_px / max_side
            new_size = (
                max(1, round(converted.width * scale)),
                max(1, round(converted.height * scale)),
            )
            converted = converted.resize(new_size, Image.Resampling.LANCZOS)

        buffer = BytesIO()
        if output_format == "jpeg":
            converted.save(
                buffer,
                format="JPEG",
                quality=preprocess_options.quality,
                optimize=True,
            )
            mime_type = "image/jpeg"
        else:
            converted.save(buffer, format="PNG", optimize=True)
            mime_type = "image/png"
        return PreprocessedVisionImage(
            data=buffer.getvalue(),
            mime_type=mime_type,
            width=converted.width,
            height=converted.height,
            original_width=original_width,
            original_height=original_height,
            source_mime_type=source_mime_type,
        )
    except UnidentifiedImageError as exc:
        raise ImagePreprocessingError("image preprocessing failed") from exc
    except OSError as exc:
        raise ImagePreprocessingError("image preprocessing failed") from exc


def _validate_media_file(media_item: dict[str, Any]) -> None:
    file_path = Path(str(media_item["file_path"]))
    detect_image_mime(file_path, media_item.get("mime_type"))


def detect_image_mime(file_path: Path, configured_mime_type: object = None) -> str:
    """Return a supported image MIME type or raise a safe unsupported-format error."""

    suffix = file_path.suffix.casefold()
    if suffix in _SUPPORTED_IMAGE_MIME_BY_SUFFIX:
        return _SUPPORTED_IMAGE_MIME_BY_SUFFIX[suffix]
    if isinstance(configured_mime_type, str):
        mime_type = configured_mime_type.strip().casefold()
        if mime_type in _SUPPORTED_IMAGE_MIME_TYPES:
            return mime_type
    raise UnsupportedImageFormat("unsupported image format")


def _store_photo_annotation(
    storage: Storage,
    media_item_id: int,
    annotation: PhotoAnnotation,
) -> int:
    data = {
        "annotation_phase": "3-B",
        "objects": list(annotation.objects),
        "ocr_text": annotation.ocr_text,
        "raw": annotation.raw,
    }
    with storage.transaction():
        if _has_vision_annotation(storage, media_item_id):
            return 0
        return storage.media_annotations.insert(
            {
                "media_item_id": media_item_id,
                "annotation_type": ANNOTATION_TYPE,
                "source": ANNOTATION_SOURCE,
                "value_text": annotation.caption,
                "data_json": json.dumps(data, ensure_ascii=False, sort_keys=True),
                "confidence": annotation.confidence,
                "model_id": annotation.model_id,
            },
        )


def _has_vision_annotation(storage: Storage, media_item_id: int) -> bool:
    row = storage.connection.execute(
        """
        SELECT 1
        FROM media_annotations
        WHERE media_item_id = ?
          AND annotation_type = ?
          AND is_excluded = 0
        LIMIT 1
        """,
        (media_item_id, ANNOTATION_TYPE),
    ).fetchone()
    return row is not None


def _extract_annotation_payload(response: VisionResponse) -> dict[str, Any]:
    if isinstance(response.raw, dict):
        for key in ("annotation", "photo_annotation"):
            value = response.raw.get(key)
            if isinstance(value, dict):
                return value
    text = response.text.strip()
    if text.startswith("{") and text.endswith("}"):
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return {}
        if isinstance(value, dict):
            return value
    return {}


def _redacted_raw_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    for key, value in payload.items():
        if key in {"caption", "objects", "ocr_text", "ocr", "confidence"}:
            continue
        raw[str(key)] = value
    return raw


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _batched(items: list[dict[str, Any]], batch_size: int):
    for start in range(0, len(items), batch_size):
        yield items[start : start + batch_size]


def _error_detail(
    media_item_id: int,
    exc: BaseException,
    context: dict[str, Any],
) -> PhotoAnnotationErrorDetail:
    return PhotoAnnotationErrorDetail(
        media_item_id=media_item_id,
        error_class=exc.__class__.__name__,
        message=_safe_error_message(exc),
        stack_summary=_safe_stack_summary(exc),
        image_format=context.get("image_format"),
        dimensions=context.get("dimensions"),
        preprocessing_succeeded=context.get("preprocessing_succeeded"),
    )


def _record_annotation_error(
    storage: Storage,
    media_item_id: int,
    detail: PhotoAnnotationErrorDetail,
    *,
    model_id: str,
) -> None:
    payload = detail.to_dict()
    payload["model_id"] = model_id
    payload.pop("media_item_id", None)
    storage.audit_log.insert(
        {
            "action": PHOTO_ANNOTATION_ERROR_ACTION,
            "actor": "pma",
            "target_table": "media_items",
            "target_id": media_item_id,
            "status": "error",
            "detail_json": json.dumps(payload, ensure_ascii=False, sort_keys=True),
        },
    )


def _safe_media_context(media_item: dict[str, Any]) -> dict[str, Any]:
    return {
        "image_format": _safe_image_format(media_item),
        "dimensions": _safe_dimensions(media_item.get("width"), media_item.get("height")),
        "preprocessing_succeeded": False,
    }


def _context_from_preprocessed(processed: PreprocessedVisionImage) -> dict[str, Any]:
    return {
        "image_format": processed.mime_type,
        "dimensions": f"{processed.width}x{processed.height}",
        "preprocessing_succeeded": True,
    }


def _safe_image_format(media_item: dict[str, Any]) -> str | None:
    mime_type = media_item.get("mime_type")
    if isinstance(mime_type, str) and mime_type.strip():
        return mime_type.strip().casefold()[:64]
    file_path = media_item.get("file_path")
    if file_path:
        suffix = Path(str(file_path)).suffix.casefold()
        if suffix:
            return suffix[:16]
    return None


def _safe_dimensions(width: object, height: object) -> str | None:
    try:
        safe_width = int(width)
        safe_height = int(height)
    except (TypeError, ValueError):
        return None
    if safe_width <= 0 or safe_height <= 0:
        return None
    return f"{safe_width}x{safe_height}"


def _safe_error_message(exc: BaseException) -> str:
    if isinstance(exc, UnsupportedImageFormat):
        return "unsupported image format"
    if isinstance(exc, ImagePreprocessingError):
        return "image preprocessing failed"
    if isinstance(exc, OSError):
        return "source file could not be read"
    message = str(exc).strip()
    if not message:
        message = exc.__class__.__name__
    if "/" in message or "\\" in message:
        return "annotation request failed"
    return message[:160]


def _safe_stack_summary(exc: BaseException) -> str:
    frames = traceback.extract_tb(exc.__traceback__)[-4:]
    names = [frame.name for frame in frames if frame.name]
    return " > ".join(names)
