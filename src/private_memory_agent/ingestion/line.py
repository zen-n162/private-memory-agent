"""Read-only LINE text export parsing and ingestion."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from private_memory_agent.storage import Storage, initialize_database

SOURCE_TYPE = "line_export"
DATE_HEADER_PATTERNS = (
    re.compile(r"^(?P<year>\d{4})[/-](?P<month>\d{1,2})[/-](?P<day>\d{1,2})(?:\([^)]+\))?$"),
    re.compile(r"^(?P<year>\d{4})\.(?P<month>\d{1,2})\.(?P<day>\d{1,2})(?:\.|\([^)]+\))?$"),
    re.compile(r"^(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日(?:\([^)]+\))?$"),
)
TIME_PATTERN = re.compile(r"^(?:(?P<ampm>午前|午後|AM|PM)\s*)?(?P<hour>\d{1,2}):(?P<minute>\d{2})$")
ROOM_PATTERNS = (
    re.compile(r"^トーク履歴[:：]\s*(?P<room>.+)$"),
    re.compile(r"^(?P<room>.+?)とのトーク履歴$"),
    re.compile(r"^Chat history with\s+(?P<room>.+)$", re.IGNORECASE),
    re.compile(r"^\[LINE\].*?[:：]\s*(?P<room>.+)$"),
)
SYSTEM_HINTS = (
    "[LINE]",
    "トーク履歴",
    "保存日時",
    "saved",
    "joined",
    "left",
    "招待",
    "参加",
    "退出",
    "退会",
)
OMITTED_TEXT_VALUES = {
    "[スタンプ]",
    "スタンプ",
    "[写真]",
    "写真",
    "[画像]",
    "画像",
    "[動画]",
    "動画",
    "[ファイル]",
    "ファイル",
    "[音声メッセージ]",
    "音声メッセージ",
    "image omitted",
    "photo",
    "video",
    "sticker",
}


@dataclass
class LineMessageRecord:
    """Parsed LINE message record."""

    room_name: str | None
    message_date: str | None
    message_time: str | None
    sent_at: str | None
    speaker: str | None
    text: str
    message_type: str
    line_number: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LineIngestResult:
    """Summary-only result safe for CLI output."""

    files_scanned: int = 0
    messages_parsed: int = 0
    messages_imported: int = 0
    skipped_duplicates: int = 0
    errors: int = 0
    dry_run: bool = False


def ingest_line_exports(
    path: Path | str,
    *,
    db_path: Path | str | None = None,
    dry_run: bool = False,
) -> LineIngestResult:
    """Parse LINE text exports and optionally store structured messages."""

    source_path = Path(path).expanduser()
    if not source_path.exists():
        raise ValueError("LINE ingest path must exist")

    storage: Storage | None = None
    if not dry_run:
        storage = initialize_database(Path(db_path or "data/local/private_memory_agent.sqlite3").expanduser())

    files_scanned = 0
    messages_parsed = 0
    messages_imported = 0
    skipped_duplicates = 0
    errors = 0

    try:
        for export_path in iter_line_export_files(source_path):
            files_scanned += 1
            try:
                raw_bytes = export_path.read_bytes()
                text = decode_line_export(raw_bytes)
                parsed = parse_line_export_text(text, source_label=str(export_path.resolve()))
            except (OSError, UnicodeError):
                errors += 1
                continue

            messages_parsed += len(parsed.messages)
            if dry_run:
                messages_imported += len(parsed.messages)
                continue

            assert storage is not None
            imported, duplicates = _store_line_export(storage, export_path, raw_bytes, parsed)
            messages_imported += imported
            skipped_duplicates += duplicates
    finally:
        if storage is not None:
            storage.close()

    return LineIngestResult(
        files_scanned=files_scanned,
        messages_parsed=messages_parsed,
        messages_imported=messages_imported,
        skipped_duplicates=skipped_duplicates,
        errors=errors,
        dry_run=dry_run,
    )


@dataclass(frozen=True)
class ParsedLineExport:
    """Parsed LINE export file."""

    room_name: str | None
    messages: tuple[LineMessageRecord, ...]


def parse_line_export_text(text: str, *, source_label: str = "line-export") -> ParsedLineExport:
    """Parse common Japanese LINE text export rows.

    This parser favors preserving text over dropping uncertain rows.
    """

    lines = text.splitlines()
    room_name = infer_room_name(lines)
    current_date: str | None = None
    messages: list[LineMessageRecord] = []
    current_message: LineMessageRecord | None = None

    for index, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip("\r")
        if line == "":
            if current_message is not None:
                current_message.text += "\n"
            continue

        parsed_date = parse_date_header(line)
        if parsed_date is not None:
            current_date = parsed_date
            current_message = None
            continue

        parsed_message = parse_message_line(
            line,
            room_name=room_name,
            current_date=current_date,
            line_number=index,
            source_label=source_label,
        )
        if parsed_message is not None:
            messages.append(parsed_message)
            current_message = parsed_message
            continue

        if current_message is not None and current_message.message_type == "text":
            current_message.text += "\n" + normalize_text(line)
            current_message.metadata["multiline"] = True
            continue

        message_type = "system" if looks_like_system_line(line) else "malformed"
        messages.append(
            LineMessageRecord(
                room_name=room_name,
                message_date=current_date,
                message_time=None,
                sent_at=None,
                speaker=None,
                text=normalize_text(line),
                message_type=message_type,
                line_number=index,
                metadata={"parser": "line_text_v1", "source_label_hash": stable_hash(source_label)},
            ),
        )
        current_message = None

    return ParsedLineExport(room_name=room_name, messages=tuple(messages))


def iter_line_export_files(path: Path):
    """Yield candidate LINE text export files without following symlink directories."""

    if path.is_file():
        if path.suffix.lower() == ".txt":
            yield path
        return
    if not path.is_dir():
        return
    for current_root, dirnames, filenames in os.walk(path, followlinks=False):
        dirnames.sort()
        for filename in sorted(filenames):
            candidate = Path(current_root) / filename
            if candidate.suffix.lower() == ".txt":
                yield candidate


def decode_line_export(raw_bytes: bytes) -> str:
    """Decode LINE export text using common local encodings."""

    for encoding in ("utf-8-sig", "utf-16", "cp932"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def infer_room_name(lines: list[str]) -> str | None:
    for line in lines[:12]:
        stripped = line.strip()
        if not stripped:
            continue
        for pattern in ROOM_PATTERNS:
            match = pattern.match(stripped)
            if match:
                return normalize_text(match.group("room"))
    return None


def parse_date_header(line: str) -> str | None:
    stripped = line.strip()
    for pattern in DATE_HEADER_PATTERNS:
        match = pattern.match(stripped)
        if not match:
            continue
        year = int(match.group("year"))
        month = int(match.group("month"))
        day = int(match.group("day"))
        return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def parse_message_line(
    line: str,
    *,
    room_name: str | None,
    current_date: str | None,
    line_number: int,
    source_label: str,
) -> LineMessageRecord | None:
    parts = line.split("\t")
    if len(parts) < 2:
        return None
    message_time = normalize_time(parts[0].strip())
    if message_time is None:
        return None

    if len(parts) >= 3:
        speaker = normalize_text(parts[1]) or None
        text = normalize_text("\t".join(parts[2:]))
    else:
        speaker = None
        text = normalize_text(parts[1])

    message_type = classify_message_text(text, speaker=speaker)
    sent_at = f"{current_date}T{message_time}:00" if current_date else None
    return LineMessageRecord(
        room_name=room_name,
        message_date=current_date,
        message_time=message_time,
        sent_at=sent_at,
        speaker=speaker,
        text=text,
        message_type=message_type,
        line_number=line_number,
        metadata={
            "parser": "line_text_v1",
            "source_label_hash": stable_hash(source_label),
            "has_date_header": current_date is not None,
        },
    )


def normalize_text(value: str) -> str:
    """Small text normalization hook."""

    return value.strip("\ufeff")


def normalize_time(value: str) -> str | None:
    match = TIME_PATTERN.match(value)
    if not match:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    ampm = match.group("ampm")
    if ampm in {"午後", "PM"} and hour < 12:
        hour += 12
    if ampm in {"午前", "AM"} and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def classify_message_text(text: str, *, speaker: str | None) -> str:
    normalized = text.strip()
    lowered = normalized.lower()
    if speaker is None:
        return "system"
    if lowered in OMITTED_TEXT_VALUES or normalized in OMITTED_TEXT_VALUES:
        return "omitted"
    if normalized.startswith("[") and normalized.endswith("]"):
        inner = normalized.strip("[]")
        if inner in OMITTED_TEXT_VALUES:
            return "omitted"
    return "text"


def looks_like_system_line(line: str) -> bool:
    lowered = line.lower()
    return any(hint.lower() in lowered for hint in SYSTEM_HINTS)


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _store_line_export(
    storage: Storage,
    export_path: Path,
    raw_bytes: bytes,
    parsed: ParsedLineExport,
) -> tuple[int, int]:
    source_uri = str(export_path.resolve())
    content_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    existing_source = storage.source_items.get_by_source_uri(
        source_type=SOURCE_TYPE,
        source_uri=source_uri,
    )
    if existing_source is None:
        with storage.transaction():
            source_item_id = storage.source_items.insert_source(
                source_type=SOURCE_TYPE,
                source_uri=source_uri,
                content_sha256=content_sha256,
                title=parsed.room_name,
                metadata_json=json.dumps(
                    {
                        "ingest_phase": "1-C",
                        "room_name": parsed.room_name,
                        "parser": "line_text_v1",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
    else:
        source_item_id = int(existing_source["id"])

    conversation_id = stable_hash(parsed.room_name or source_uri)
    imported = 0
    duplicates = 0
    for ordinal, message in enumerate(parsed.messages, start=1):
        message_id = stable_hash(f"{source_uri}:{ordinal}:{message.line_number}:{message.sent_at}:{message.text}")
        if storage.line_messages.get_by_message_id(
            source_item_id=source_item_id,
            message_id=message_id,
        ):
            duplicates += 1
            continue
        metadata = dict(message.metadata)
        metadata.update(
            {
                "room_name": message.room_name,
                "message_date": message.message_date,
                "message_time": message.message_time,
                "line_number": message.line_number,
            },
        )
        storage.line_messages.insert_message(
            source_item_id=source_item_id,
            conversation_id=conversation_id,
            message_id=message_id,
            sender_id=message.speaker,
            sent_at=message.sent_at,
            message_type=message.message_type,
            body_text=message.text,
            metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        )
        imported += 1

    return imported, duplicates
