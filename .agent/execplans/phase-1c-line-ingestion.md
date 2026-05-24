# ExecPlan: Phase 1-C LINE Chat Export Ingestion

## Goal

Add `pma ingest line --path <file-or-folder>` to parse local LINE text exports into structured messages stored in SQLite. The importer should preserve original message text in `line_messages.body_text`, but CLI/log output must remain privacy-safe and never print raw message content.

## Non-goals

- Do not parse encrypted LINE backups.
- Do not use external or unofficial LINE access.
- Do not ingest real user LINE data in tests.
- Do not call AI models, embeddings, OCR, or external APIs.
- Do not log raw LINE message text, speaker names, filenames, or source paths in normal command output.
- Do not implement semantic normalization beyond simple hooks.

## Current state

The repository has a local SQLite storage layer with `source_items` and `line_messages`. Photo ingestion already established a CLI pattern under `pma ingest`. Config supports local raw source paths, but real data checks and ingestion are explicit only. There is no LINE parser or importer yet.

## Proposed design

Add a LINE ingestion module:

- `parse_line_export_text(text, source_label)` parses one text export.
- `iter_line_export_files(path)` accepts either a single file or a folder and recursively finds `.txt` files.
- `ingest_line_exports(path, db_path, dry_run=False)` parses one or more exports and stores rows.

Parser assumptions:

- Japanese LINE text exports commonly contain date header lines such as `2024/01/02(火)`.
- Message lines commonly use tabs: `12:34\tSpeaker\tMessage`.
- Multiline messages are represented as continuation lines after the latest parsed message.
- Omitted attachment text such as `[スタンプ]`, `[写真]`, `写真`, `動画`, `画像`, and similar values are marked as `omitted`.
- Lines that cannot be parsed are preserved as `system` or `malformed` messages, depending on whether they look like export/system metadata.

Storage:

- One `source_items` row per export file.
- One `line_messages` row per parsed message.
- `conversation_id` is a deterministic SHA256-derived id from room name/source label.
- `message_id` is deterministic from source label and message ordinal.
- `sender_id` stores the speaker string when present.
- `sent_at` stores a local ISO-like datetime when date and time are available.
- `metadata_json` stores room name, source line number, parser format, and normalization metadata.

## Data contracts

`LineMessageRecord`:

- `room_name: str | None`
- `message_date: str | None`
- `message_time: str | None`
- `sent_at: str | None`
- `speaker: str | None`
- `text: str`
- `message_type: text | omitted | system | malformed`
- `line_number: int`
- `metadata: dict`

`LineIngestResult`:

- `files_scanned`
- `messages_parsed`
- `messages_imported`
- `skipped_duplicates`
- `errors`
- `dry_run`

## Files to change

- `.agent/execplans/phase-1c-line-ingestion.md`
- `docs/DATA_MODEL.md`
- `src/private_memory_agent/cli.py`
- `src/private_memory_agent/ingestion/__init__.py`
- `src/private_memory_agent/ingestion/line.py`
- `src/private_memory_agent/storage/repositories.py`
- `tests/fixtures/line_export_japanese.txt`
- `tests/test_line_ingestion.py`

## Implementation steps

1. Add parser dataclasses and helper regexes for Japanese LINE date/message rows.
2. Add simple normalization hooks for text and omitted media markers.
3. Add line export importer that writes `source_items` and `line_messages`.
4. Add duplicate detection by source export path and deterministic message id.
5. Add `pma ingest line --path <file-or-folder>`, `--db`, and `--dry-run`.
6. Add artificial Japanese fixture covering date headers, multiline text, omitted media, system lines, and malformed lines.
7. Add docs describing supported assumptions.
8. Run `pytest -q`.

## Tests and verification

Run:

- `pytest -q`

Tests use artificial Japanese text fixtures only and temporary SQLite databases. They must not require or read real LINE export paths.

## Privacy and security

The parser reads local text files only when explicitly requested. It preserves message text in SQLite but never prints it in CLI output. CLI results are count-only. Tests assert output does not include fixture message text or filenames. No source files are modified.

## Performance and hardware

The parser is streaming-friendly and CPU-only. It does not use GPU, models, network, or large memory allocations.

## Rollback

Remove the LINE ingestion module, CLI wiring, tests, fixture, and docs additions. No real source data is modified by this phase.

## Open questions

None blocking for Phase 1-C.
