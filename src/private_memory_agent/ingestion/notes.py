"""Read-only notes export ingestion."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from private_memory_agent.storage import Storage, initialize_database

SOURCE_TYPE = "note_export"
SUPPORTED_EXTENSIONS = {".md", ".markdown", ".txt", ".json", ".pdf"}
TEXT_EXTENSIONS = {".md", ".markdown", ".txt"}
PDF_PLACEHOLDER_BODY = "[PDF text extraction not available in Phase 1-D]"


@dataclass(frozen=True)
class NoteDocument:
    """Parsed note export document."""

    path: Path
    title: str
    body: str
    content_sha256: str
    created_at_source: str | None
    updated_at_source: str | None
    metadata: dict[str, Any]

    @property
    def note_id(self) -> str:
        return stable_hash(f"{self.path}:{self.content_sha256}")

    @property
    def metadata_json(self) -> str:
        return json.dumps(self.metadata, ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class NoteIngestResult:
    """Summary-only notes ingest result safe for CLI output."""

    files_scanned: int = 0
    notes_parsed: int = 0
    notes_imported: int = 0
    skipped_duplicates: int = 0
    skipped_unsupported: int = 0
    errors: int = 0
    dry_run: bool = False


def ingest_notes(
    path: Path | str,
    *,
    db_path: Path | str | None = None,
    dry_run: bool = False,
) -> NoteIngestResult:
    """Parse notes from a file or folder and optionally store them."""

    source_path = Path(path).expanduser()
    if not source_path.exists():
        raise ValueError("notes ingest path must exist")

    storage: Storage | None = None
    if not dry_run:
        storage = initialize_database(Path(db_path or "data/local/private_memory_agent.sqlite3").expanduser())

    files_scanned = 0
    notes_parsed = 0
    notes_imported = 0
    skipped_duplicates = 0
    skipped_unsupported = 0
    errors = 0

    try:
        for candidate in iter_note_export_files(source_path):
            if candidate.suffix.lower() not in SUPPORTED_EXTENSIONS:
                skipped_unsupported += 1
                continue
            files_scanned += 1
            try:
                document = parse_note_file(candidate)
            except (OSError, UnicodeError, json.JSONDecodeError):
                errors += 1
                continue

            notes_parsed += 1
            if dry_run:
                notes_imported += 1
                continue

            assert storage is not None
            if _is_duplicate(storage, document):
                skipped_duplicates += 1
                continue
            _store_note_document(storage, document)
            notes_imported += 1
    finally:
        if storage is not None:
            storage.close()

    return NoteIngestResult(
        files_scanned=files_scanned,
        notes_parsed=notes_parsed,
        notes_imported=notes_imported,
        skipped_duplicates=skipped_duplicates,
        skipped_unsupported=skipped_unsupported,
        errors=errors,
        dry_run=dry_run,
    )


def iter_note_export_files(path: Path):
    """Yield candidate note files without following symlink directories."""

    if path.is_file():
        yield path
        return
    if not path.is_dir():
        return
    for current_root, dirnames, filenames in os.walk(path, followlinks=False):
        dirnames.sort()
        for filename in sorted(filenames):
            yield Path(current_root) / filename


def parse_note_file(path: Path) -> NoteDocument:
    extension = path.suffix.lower()
    content_sha256 = compute_sha256(path)
    stat = path.stat()
    updated_at_source = timestamp_from_epoch(stat.st_mtime)
    resolved_path = path.resolve()

    if extension in TEXT_EXTENSIONS:
        text = decode_note_text(path.read_bytes())
        frontmatter, body = split_frontmatter(text)
        title = extract_title(body, frontmatter=frontmatter, fallback=path.stem)
        created_at_source = first_present(frontmatter, "created", "created_at", "date")
        updated_from_frontmatter = first_present(frontmatter, "modified", "updated", "updated_at")
        metadata = {
            "ingest_phase": "1-D",
            "source_format": extension.lstrip("."),
            "frontmatter_keys": sorted(frontmatter),
        }
        return NoteDocument(
            path=resolved_path,
            title=title,
            body=body,
            content_sha256=content_sha256,
            created_at_source=normalize_datetime_string(created_at_source),
            updated_at_source=normalize_datetime_string(updated_from_frontmatter) or updated_at_source,
            metadata=metadata,
        )

    if extension == ".json":
        raw = json.loads(decode_note_text(path.read_bytes()))
        if not isinstance(raw, dict):
            raw = {"body": json.dumps(raw, ensure_ascii=False)}
        title = str(first_present(raw, "title", "name", "subject") or path.stem)
        body_value = first_present(raw, "body", "text", "content", "markdown", "note")
        body = body_value if isinstance(body_value, str) else json.dumps(body_value or raw, ensure_ascii=False)
        created_at_source = first_present(raw, "created", "created_at", "createdAt", "date")
        updated_from_json = first_present(raw, "modified", "updated", "updated_at", "updatedAt")
        metadata = {
            "ingest_phase": "1-D",
            "source_format": "json",
            "json_keys": sorted(str(key) for key in raw),
        }
        return NoteDocument(
            path=resolved_path,
            title=title,
            body=body,
            content_sha256=content_sha256,
            created_at_source=normalize_datetime_string(created_at_source),
            updated_at_source=normalize_datetime_string(updated_from_json) or updated_at_source,
            metadata=metadata,
        )

    if extension == ".pdf":
        return NoteDocument(
            path=resolved_path,
            title=path.stem,
            body=PDF_PLACEHOLDER_BODY,
            content_sha256=content_sha256,
            created_at_source=None,
            updated_at_source=updated_at_source,
            metadata={
                "ingest_phase": "1-D",
                "source_format": "pdf",
                "text_extraction": "placeholder",
                "text_extraction_dependency": None,
            },
        )

    raise ValueError("unsupported note file extension")


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split simple YAML-like frontmatter from Markdown/TXT content."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        return {}, normalized
    end_index = normalized.find("\n---\n", 4)
    if end_index == -1:
        return {}, normalized
    raw_frontmatter = normalized[4:end_index]
    body = normalized[end_index + len("\n---\n") :]
    frontmatter: dict[str, str] = {}
    for line in raw_frontmatter.splitlines():
        key, separator, value = line.partition(":")
        if not separator or not key.strip():
            continue
        frontmatter[key.strip()] = value.strip().strip("'\"")
    return frontmatter, body


def extract_title(body: str, *, frontmatter: dict[str, str], fallback: str) -> str:
    frontmatter_title = first_present(frontmatter, "title", "name")
    if frontmatter_title:
        return str(frontmatter_title)
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            return title or fallback
        return stripped
    return fallback


def compute_sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decode_note_text(raw_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp932"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def normalize_datetime_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if re.match(r"^\d{4}/\d{1,2}/\d{1,2}", text):
        return text.replace("/", "-", 2)
    return text


def first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def timestamp_from_epoch(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _is_duplicate(storage: Storage, document: NoteDocument) -> bool:
    source_uri = str(document.path)
    if storage.source_items.get_by_source_uri(source_type=SOURCE_TYPE, source_uri=source_uri):
        return True
    if storage.source_items.get_by_sha256(source_type=SOURCE_TYPE, content_sha256=document.content_sha256):
        return True
    return storage.notes.get_by_note_id(document.note_id) is not None


def _store_note_document(storage: Storage, document: NoteDocument) -> None:
    with storage.transaction():
        source_item_id = storage.source_items.insert_source(
            source_type=SOURCE_TYPE,
            source_uri=str(document.path),
            content_sha256=document.content_sha256,
            title=document.title,
            metadata_json=json.dumps(
                {
                    "ingest_phase": "1-D",
                    "source_format": document.metadata.get("source_format"),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        storage.notes.insert_note(
            source_item_id=source_item_id,
            note_id=document.note_id,
            title=document.title,
            body_text=document.body,
            created_at_source=document.created_at_source,
            updated_at_source=document.updated_at_source,
            metadata_json=document.metadata_json,
        )
