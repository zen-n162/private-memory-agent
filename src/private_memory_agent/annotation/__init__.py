"""Local annotation pipelines."""

from private_memory_agent.annotation.photos import (
    DEFAULT_PHOTO_ANNOTATION_PROMPT,
    ImagePreprocessingError,
    PhotoAnnotation,
    PhotoAnnotationErrorDetail,
    PhotoPreprocessOptions,
    PhotoAnnotationResult,
    PreprocessedVisionImage,
    UnsupportedImageFormat,
    annotate_photos,
    detect_image_mime,
    preprocess_image_for_vision,
    select_unannotated_photo_media,
)
from private_memory_agent.annotation.stats import (
    AnnotationStatsReport,
    FailedPhotoAnnotation,
    FailedPhotoAnnotationReport,
    PhotoAnnotationStats,
    build_annotation_stats_report,
    list_failed_photo_annotations,
)
from private_memory_agent.annotation.text import (
    ExtractedTextUnderstanding,
    TextAnnotationResult,
    TextExtractionError,
    annotate_text,
    parse_text_understanding_response,
    select_unannotated_text_items,
)

__all__ = [
    "DEFAULT_PHOTO_ANNOTATION_PROMPT",
    "ExtractedTextUnderstanding",
    "AnnotationStatsReport",
    "FailedPhotoAnnotation",
    "FailedPhotoAnnotationReport",
    "ImagePreprocessingError",
    "PhotoAnnotation",
    "PhotoAnnotationErrorDetail",
    "PhotoAnnotationStats",
    "PhotoPreprocessOptions",
    "PhotoAnnotationResult",
    "PreprocessedVisionImage",
    "TextAnnotationResult",
    "TextExtractionError",
    "UnsupportedImageFormat",
    "annotate_photos",
    "annotate_text",
    "build_annotation_stats_report",
    "detect_image_mime",
    "list_failed_photo_annotations",
    "parse_text_understanding_response",
    "preprocess_image_for_vision",
    "select_unannotated_photo_media",
    "select_unannotated_text_items",
]
