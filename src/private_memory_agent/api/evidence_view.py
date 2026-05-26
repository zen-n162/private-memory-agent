"""Privacy-safe evidence display payloads for the local UI."""

from __future__ import annotations

import io
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from private_memory_agent.retrieval.text import media_annotation_search_text

DISPLAY_SOURCES = ("photos", "line", "notes", "unknown")
DEFAULT_THUMBNAIL_MAX_SIDE = 320
MAX_THUMBNAIL_MAX_SIDE = 1024
DEFAULT_CANDIDATE_THUMBNAIL_LIMIT = 6
_PATH_LIKE_RE = re.compile(r"(?:/[^\s]+)+|[A-Za-z]:\\[^\s]+")
_PRECISE_DECIMAL_RE = re.compile(r"(?<!\d)-?\d{2,3}\.\d{4,}(?!\d)")
REASON_LABELS_JA: dict[str, str] = {
    "image_media": "写真メディアが存在します",
    "annotation_available": "画像注釈が利用可能です",
    "location_metadata_present": "位置情報の有無が手がかりになります",
    "outing_annotation_keyword": "外出を示す可能性のある注釈があります",
    "same_day_line_support": "同日のLINE記録があります",
    "same_day_note_support": "同日のノート記録があります",
    "weak_photo_annotation_or_metadata": "写真・メタデータだけでは根拠が弱いです",
    "no_plan_concept_match": "質問意図と強く一致しないため弱い根拠です",
    "low_outing_document_or_screenshot_keyword": "画面・文書系の写真らしく外出根拠として弱いです",
    "weak_metadata_only": "メタデータだけでは判断材料が不足しています",
    "temporal_line_notes_fallback_support": "写真以外の同日記録から弱く補助されています",
    "temporal_outing_photo_match": "外出らしい写真注釈と一致しています",
    "temporal_outing_day_support": "同日の記録が外出候補を補助しています",
    "event_intent_visual_signal": "イベント意図に合う画像特徴があります",
    "temporal_event_specific_photo_match": "イベントに直接関係する写真候補です",
    "temporal_event_text_match": "イベントに関係するLINE/メモ候補です",
    "visual_direct_match": "探している対象が写っている可能性が高い写真です",
    "visual_signal_match": "画像注釈の一部が検索意図と一致しています",
    "weak_visual_match": "画像注釈との一致が弱い候補です",
    "no_visual_signal_match": "画像注釈が検索意図と一致しません",
    "low_visual_document_or_screenshot_keyword": "画面・文書系の画像らしく視覚根拠として弱いです",
    "examined_candidate_not_used": "確認しましたが回答根拠には使っていません",
    "weak_or_rejected_temporal_candidate": "弱い候補として退けています",
    "generic_only_match": "一般的な語だけが一致しており根拠として弱いです",
    "weak_match": "一致が弱く、回答根拠としては慎重に扱います",
    "unrelated": "質問との関連が低いと判断されています",
}


@dataclass(frozen=True)
class EvidenceDisplayOptions:
    """Visibility controls for local evidence display payloads."""

    show_snippets: bool = False
    show_photo_thumbnails: bool = True
    show_full_text: bool = False
    snippet_chars: int = 160
    full_text_chars: int = 1000
    thumbnail_prefix: str = "/api/evidence/media"
    expanded_snippet_chars: int = 420
    thumbnail_initial_limit: int = DEFAULT_CANDIDATE_THUMBNAIL_LIMIT


class EvidenceThumbnailError(RuntimeError):
    """Safe thumbnail generation failure."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def build_evidence_display_payload(
    db_path: Path | str,
    *,
    evidence: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    answer_evidence_references: list[str] | tuple[str, ...] = (),
    candidate_dates: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    options: EvidenceDisplayOptions | None = None,
) -> dict[str, Any]:
    """Build UI-oriented evidence display data from selected evidence IDs only."""

    active_options = options or EvidenceDisplayOptions()
    base_items = [dict(item) for item in evidence]
    ordered_ids = _unique_strings(
        [
            *(str(item.get("evidence_id")) for item in base_items if item.get("evidence_id")),
            *answer_evidence_references,
            *(
                evidence_id
                for date_item in candidate_dates
                for evidence_id in _candidate_date_evidence_ids(date_item)
            ),
        ],
    )
    base_by_id = {str(item.get("evidence_id")): item for item in base_items if item.get("evidence_id")}
    details_by_id = _load_evidence_details(
        Path(db_path).expanduser(),
        ordered_ids,
        base_by_id=base_by_id,
        options=active_options,
    )
    groups: dict[str, list[dict[str, Any]]] = {source: [] for source in DISPLAY_SOURCES}
    for evidence_id in ordered_ids:
        detail = details_by_id.get(evidence_id)
        if detail is None:
            continue
        groups.setdefault(str(detail.get("source", "unknown")), []).append(detail)
    reference_groups = _group_ids_by_source(answer_evidence_references)
    rendered_dates = [
        _render_candidate_date(date_item, details_by_id)
        for date_item in candidate_dates
    ]
    return {
        "groups": groups,
        "by_id": details_by_id,
        "evidence_reference_groups": reference_groups,
        "candidate_dates": rendered_dates,
        "privacy": {
            "snippets_hidden": not active_options.show_snippets,
            "full_text_hidden": not active_options.show_full_text,
            "photo_thumbnails_hidden": not active_options.show_photo_thumbnails,
            "paths_hidden": True,
            "gps_hidden": True,
            "exif_hidden": True,
            "raw_model_output_hidden": True,
            "thumbnail_initial_limit": active_options.thumbnail_initial_limit,
        },
    }


def create_media_thumbnail(
    db_path: Path | str,
    media_item_id: int,
    *,
    max_side: int = DEFAULT_THUMBNAIL_MAX_SIDE,
) -> tuple[bytes, str]:
    """Return a resized JPEG thumbnail for one indexed media item."""

    safe_max_side = max(32, min(int(max_side), MAX_THUMBNAIL_MAX_SIDE))
    row = _fetch_media_item_row(Path(db_path).expanduser(), int(media_item_id))
    if row is None:
        raise EvidenceThumbnailError("media item was not found", status_code=404)
    media_type = str(row["media_type"] or "").lower()
    mime_type = str(row["mime_type"] or "").lower()
    if media_type != "image" and not mime_type.startswith("image/"):
        raise EvidenceThumbnailError("media item is not an image", status_code=415)
    path_value = row["file_path"]
    if not path_value:
        raise EvidenceThumbnailError("media file is unavailable", status_code=404)
    image_path = Path(str(path_value)).expanduser()
    if not image_path.exists() or not image_path.is_file():
        raise EvidenceThumbnailError("media file is unavailable", status_code=404)
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:  # pragma: no cover - Pillow is a project dependency.
        raise EvidenceThumbnailError("thumbnail support is unavailable", status_code=503) from exc
    try:
        with Image.open(image_path) as image:
            image.thumbnail((safe_max_side, safe_max_side))
            output_image = image.convert("RGB")
            buffer = io.BytesIO()
            output_image.save(buffer, format="JPEG", quality=82, optimize=True)
            return buffer.getvalue(), "image/jpeg"
    except (OSError, UnidentifiedImageError) as exc:
        raise EvidenceThumbnailError("thumbnail could not be generated", status_code=415) from exc


def _load_evidence_details(
    db_path: Path,
    evidence_ids: list[str],
    *,
    base_by_id: dict[str, dict[str, Any]],
    options: EvidenceDisplayOptions,
) -> dict[str, dict[str, Any]]:
    if not db_path.exists():
        return {
            evidence_id: _fallback_detail(evidence_id, base_by_id.get(evidence_id))
            for evidence_id in evidence_ids
        }
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        details: dict[str, dict[str, Any]] = {}
        for evidence_id in evidence_ids:
            table, row_id = _parse_evidence_id(evidence_id)
            base = base_by_id.get(evidence_id, {})
            if table == "media_items" and row_id is not None:
                details[evidence_id] = _photo_detail(connection, row_id, evidence_id, base, options)
            elif table == "line_messages" and row_id is not None:
                details[evidence_id] = _line_detail(connection, row_id, evidence_id, base, options)
            elif table == "notes" and row_id is not None:
                details[evidence_id] = _note_detail(connection, row_id, evidence_id, base, options)
            else:
                details[evidence_id] = _fallback_detail(evidence_id, base)
        return details
    finally:
        connection.close()


def _photo_detail(
    connection: sqlite3.Connection,
    media_item_id: int,
    evidence_id: str,
    base: dict[str, Any],
    options: EvidenceDisplayOptions,
) -> dict[str, Any]:
    if not _table_exists(connection, "media_items"):
        return _fallback_detail(evidence_id, base)
    row = connection.execute(
        """
        SELECT id, media_type, mime_type, width, height, taken_at
        FROM media_items
        WHERE id = ? AND is_excluded = 0
        LIMIT 1
        """,
        (media_item_id,),
    ).fetchone()
    if row is None:
        return _fallback_detail(evidence_id, base)
    annotation_summary: str | None = None
    annotation_summary_full: str | None = None
    if options.show_snippets and _table_exists(connection, "media_annotations"):
        annotation = connection.execute(
            """
            SELECT value_text, data_json
            FROM media_annotations
            WHERE media_item_id = ?
              AND annotation_type = 'vision'
              AND is_excluded = 0
            ORDER BY id DESC
            LIMIT 1
            """,
            (media_item_id,),
        ).fetchone()
        if annotation is not None:
            annotation_text = media_annotation_search_text(annotation["value_text"], annotation["data_json"])
            annotation_summary = _safe_truncate(
                annotation_text,
                options.snippet_chars,
            )
            annotation_summary_full = _safe_truncate(annotation_text, _expanded_text_limit(options))
    detail = _base_detail(evidence_id, "photos", base)
    detail.update(
        {
            "media_item_id": media_item_id,
            "media_type": row["media_type"],
            "mime_type": row["mime_type"],
            "taken_at": row["taken_at"],
            "width": row["width"],
            "height": row["height"],
            "thumbnail_url": f"{options.thumbnail_prefix}/{media_item_id}/thumbnail"
            if options.show_photo_thumbnails
            else None,
            "annotation_summary": annotation_summary,
            "annotation_summary_full_preview": annotation_summary_full,
            "annotation_summary_has_more": bool(annotation_summary_full and annotation_summary_full != annotation_summary),
            "annotation_summary_hidden": annotation_summary is None,
        },
    )
    return detail


def _line_detail(
    connection: sqlite3.Connection,
    line_message_id: int,
    evidence_id: str,
    base: dict[str, Any],
    options: EvidenceDisplayOptions,
) -> dict[str, Any]:
    if not _table_exists(connection, "line_messages"):
        return _fallback_detail(evidence_id, base)
    row = connection.execute(
        """
        SELECT id, sent_at, sender_id, body_text, normalized_text
        FROM line_messages
        WHERE id = ? AND is_excluded = 0
        LIMIT 1
        """,
        (line_message_id,),
    ).fetchone()
    if row is None:
        return _fallback_detail(evidence_id, base)
    snippet = None
    full_preview = None
    if options.show_snippets:
        raw_text = row["body_text"] or row["normalized_text"] or ""
        snippet = _safe_truncate(raw_text, options.snippet_chars)
        full_preview = _safe_truncate(raw_text, _expanded_text_limit(options))
    detail = _base_detail(evidence_id, "line", base)
    detail.update(
        {
            "line_message_id": line_message_id,
            "timestamp": row["sent_at"],
            "speaker": _safe_truncate(row["sender_id"] or "", 80) if options.show_snippets else None,
            "speaker_hidden": not options.show_snippets,
            "snippet": snippet,
            "snippet_preview": snippet,
            "snippet_full_preview": full_preview,
            "snippet_has_more": bool(full_preview and full_preview != snippet),
            "snippet_chars": options.snippet_chars,
            "snippet_hidden": snippet is None,
        },
    )
    return detail


def _note_detail(
    connection: sqlite3.Connection,
    note_id: int,
    evidence_id: str,
    base: dict[str, Any],
    options: EvidenceDisplayOptions,
) -> dict[str, Any]:
    if not _table_exists(connection, "notes"):
        return _fallback_detail(evidence_id, base)
    row = connection.execute(
        """
        SELECT id, title, body_text, normalized_text, created_at_source, updated_at_source
        FROM notes
        WHERE id = ? AND is_excluded = 0
        LIMIT 1
        """,
        (note_id,),
    ).fetchone()
    if row is None:
        return _fallback_detail(evidence_id, base)
    title = None
    snippet = None
    full_preview = None
    if options.show_snippets:
        title = _safe_truncate(row["title"] or "", 120)
        raw_text = row["body_text"] or row["normalized_text"] or ""
        snippet = _safe_truncate(raw_text, options.snippet_chars)
        full_preview = _safe_truncate(raw_text, _expanded_text_limit(options))
    detail = _base_detail(evidence_id, "notes", base)
    detail.update(
        {
            "note_id": note_id,
            "title": title,
            "title_hidden": title is None,
            "timestamp": row["updated_at_source"] or row["created_at_source"],
            "snippet": snippet,
            "snippet_preview": snippet,
            "snippet_full_preview": full_preview,
            "snippet_has_more": bool(full_preview and full_preview != snippet),
            "snippet_chars": options.snippet_chars,
            "snippet_hidden": snippet is None,
        },
    )
    return detail


def _base_detail(evidence_id: str, source: str, base: dict[str, Any]) -> dict[str, Any]:
    should_use = base.get("should_use")
    used_by_answer = bool(base.get("used_by_answer")) and should_use is not False
    role = str(base.get("evidence_role") or "")
    if should_use is False:
        role = "rejected"
        used_by_answer = False
    elif not role:
        role = "used" if used_by_answer else "candidate"
    reason_category = base.get("reason_category")
    return {
        "evidence_id": evidence_id,
        "source": source,
        "source_type": source,
        "evidence_role": role,
        "should_use": should_use,
        "specificity": base.get("specificity"),
        "relevance_score": base.get("relevance_score"),
        "reason_category": reason_category,
        "reason_label": reason_label_for_code(reason_category),
        "used_by_answer": used_by_answer,
        "occurred_at": base.get("occurred_at"),
        "matched_visual_signals": list(base.get("matched_visual_signals") or []),
        "source_methods": list(base.get("source_methods") or []),
        "verification_status": base.get("verification_status"),
    }


def _fallback_detail(evidence_id: str, base: dict[str, Any] | None) -> dict[str, Any]:
    source = _source_from_evidence_id(evidence_id)
    return _base_detail(evidence_id, source, base or {})


def _render_candidate_date(
    candidate_date: dict[str, Any],
    details_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    top_ids = _unique_strings(candidate_date.get("top_evidence_ids") or ())
    support_ids = _unique_strings(candidate_date.get("support_evidence_ids") or ())
    candidate_ids = _unique_strings(candidate_date.get("candidate_evidence_ids") or ())
    rejected_ids = _unique_strings(candidate_date.get("rejected_evidence_ids") or ())
    used_ids = _unique_strings([*top_ids, *support_ids])
    used_details = _details_for_ids(used_ids, details_by_id)
    candidate_details = _details_for_ids(candidate_ids, details_by_id)
    rejected_details = _details_for_ids(rejected_ids, details_by_id)
    grouped_used = _group_details_by_source(used_details)
    grouped_candidate = _group_details_by_source(candidate_details)
    reason_codes = _reason_codes(candidate_date.get("reason"))
    reason_labels = [reason_label_for_code(code) for code in reason_codes]
    used_evidence = used_details
    candidate_evidence = candidate_details
    rejected_evidence = rejected_details
    photos = [*grouped_used["photos"], *grouped_candidate["photos"]]
    line_snippets = [*grouped_used["line"], *grouped_candidate["line"]]
    note_snippets = [*grouped_used["notes"], *grouped_candidate["notes"]]
    return {
        "date": candidate_date.get("date"),
        "confidence": candidate_date.get("confidence"),
        "event_score": candidate_date.get("event_score"),
        "matched_visual_signals": candidate_date.get("matched_visual_signals", []),
        "matched_textual_signals": candidate_date.get("matched_textual_signals", []),
        "matched_visual_signal_count": candidate_date.get("matched_visual_signal_count", 0),
        "matched_textual_signal_count": candidate_date.get("matched_textual_signal_count", 0),
        "reason": candidate_date.get("reason"),
        "reason_codes": reason_codes,
        "reason_labels": reason_labels,
        "reason_summary": " / ".join(reason_labels) if reason_labels else candidate_date.get("reason"),
        "photo_count": candidate_date.get("photo_count", 0),
        "annotated_photo_count": candidate_date.get("annotated_photo_count", 0),
        "line_support_count": candidate_date.get("line_support_count", 0),
        "notes_support_count": candidate_date.get("notes_support_count", 0),
        "used_evidence_count": len(used_ids),
        "supporting_photos": grouped_used["photos"],
        "supporting_line_snippets": grouped_used["line"],
        "supporting_note_snippets": grouped_used["notes"],
        "used_evidence": used_evidence,
        "candidate_evidence": candidate_evidence,
        "rejected_evidence": rejected_evidence,
        "photos": photos,
        "line_snippets": line_snippets,
        "note_snippets": note_snippets,
        "thumbnail_initial_limit": DEFAULT_CANDIDATE_THUMBNAIL_LIMIT,
        "evidence_ids": {
            "used": used_ids,
            "candidate": candidate_ids,
            "rejected": rejected_ids,
        },
    }


def _details_for_ids(
    evidence_ids: list[str],
    details_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    return [details_by_id[evidence_id] for evidence_id in evidence_ids if evidence_id in details_by_id]


def _group_details_by_source(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {source: [] for source in DISPLAY_SOURCES}
    for item in items:
        groups.setdefault(str(item.get("source", "unknown")), []).append(item)
    return groups


def _group_ids_by_source(evidence_ids: list[str] | tuple[str, ...]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {source: [] for source in DISPLAY_SOURCES}
    for evidence_id in evidence_ids:
        groups.setdefault(_source_from_evidence_id(evidence_id), []).append(evidence_id)
    return groups


def reason_label_for_code(value: Any) -> str | None:
    """Return a human-readable Japanese reason label for UI display."""

    code = str(value or "").strip()
    if not code:
        return None
    return REASON_LABELS_JA.get(code, code.replace("_", " "))


def _reason_codes(value: Any) -> list[str]:
    if not value:
        return []
    return _unique_strings(
        [
            part.strip()
            for part in str(value).split(",")
            if part.strip()
        ],
    )


def _candidate_date_evidence_ids(candidate_date: dict[str, Any]) -> list[str]:
    return _unique_strings(
        [
            *(candidate_date.get("top_evidence_ids") or ()),
            *(candidate_date.get("support_evidence_ids") or ()),
            *(candidate_date.get("candidate_evidence_ids") or ()),
            *(candidate_date.get("rejected_evidence_ids") or ()),
        ],
    )


def _fetch_media_item_row(db_path: Path, media_item_id: int) -> sqlite3.Row | None:
    if not db_path.exists():
        return None
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        if not _table_exists(connection, "media_items"):
            return None
        return connection.execute(
            """
            SELECT id, media_type, mime_type, file_path
            FROM media_items
            WHERE id = ? AND is_excluded = 0
            LIMIT 1
            """,
            (media_item_id,),
        ).fetchone()
    finally:
        connection.close()


def _parse_evidence_id(evidence_id: str) -> tuple[str, int | None]:
    table, _, raw_id = str(evidence_id).partition(":")
    try:
        return table, int(raw_id)
    except ValueError:
        return table, None


def _source_from_evidence_id(evidence_id: str) -> str:
    if evidence_id.startswith("media_items:") or evidence_id.startswith("media_annotations:"):
        return "photos"
    if evidence_id.startswith("line_messages:"):
        return "line"
    if evidence_id.startswith("notes:"):
        return "notes"
    return "unknown"


def _safe_truncate(value: Any, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = _PATH_LIKE_RE.sub("[path redacted]", text)
    text = _PRECISE_DECIMAL_RE.sub("[coordinate redacted]", text)
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3].rstrip() + "..."


def _expanded_text_limit(options: EvidenceDisplayOptions) -> int:
    if options.show_full_text:
        return options.full_text_chars
    return max(options.snippet_chars, min(options.expanded_snippet_chars, options.full_text_chars))


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _unique_strings(values: list[Any] | tuple[Any, ...]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result
