# ExecPlan: Phase 1-D Notes Ingestion

## Goal

Add `pma ingest notes --path <file-or-folder>` to import local note exports as read-only source data. Markdown, TXT, and JSON exports should preserve full note content. PDF files should be represented with metadata and placeholder body text unless a lightweight local parser is later introduced.

## Non-goals

- Do not summarize, classify, embed, or otherwise call AI models.
- Do not use network services or external note APIs.
- Do not parse private fixtures or require real local notes in tests.
- Do not log note body text, filenames, source paths, or private metadata in normal CLI output.
- Do not modify, move, rename, delete, or write beside source files.
- Do not implement vector DB or retrieval.

## Current state

SQLite storage exists with `source_items` and `notes`. The CLI supports `pma ingest photos` and `pma ingest line`, each with `--path`, `--configured`, `--db`, and `--dry-run`. Config already includes a `notes` raw source category. There is no notes parser/importer yet.

## Proposed design

Add `private_memory_agent.ingestion.notes` with:

- `parse_note_file(path)` for `.md`, `.markdown`, `.txt`, `.json`, and `.pdf`.
- `iter_note_export_files(path)` for a single file or recursive folder scan.
- `ingest_notes(path, db_path, dry_run=False)` for parsing and optional SQLite writes.

Parsing:

- Markdown and TXT: decode text locally, extract YAML-like frontmatter when present, title from frontmatter title, first Markdown heading, first non-empty line, or file stem.
- JSON: parse common object fields (`title`, `body`, `text`, `content`, `markdown`, `created_at`, `updated_at`, etc.) and preserve content as body text where possible.
- PDF: store title from filename and placeholder body text. No PDF dependency is added in this phase.

Storage:

- One `source_items` row per file with `source_type = "note_export"`.
- One `notes` row per imported file.
- Duplicate policy skips if source path or content hash already exists.

CLI output is count-only: files scanned, notes parsed/imported, duplicates, unsupported files, and errors.

## Data contracts

`NoteDocument`:

- `path: Path`
- `title: str`
- `body: str`
- `content_sha256: str`
- `created_at_source: str | None`
- `updated_at_source: str | None`
- `metadata: dict`

`NoteIngestResult`:

- `files_scanned`
- `notes_parsed`
- `notes_imported`
- `skipped_duplicates`
- `skipped_unsupported`
- `errors`
- `dry_run`

## Files to change

- `.agent/execplans/phase-1d-notes-ingestion.md`
- `src/private_memory_agent/cli.py`
- `src/private_memory_agent/ingestion/__init__.py`
- `src/private_memory_agent/ingestion/notes.py`
- `src/private_memory_agent/storage/repositories.py`
- `docs/DATA_MODEL.md`
- `tests/fixtures/note_japanese.md`
- `tests/fixtures/note_japanese.txt`
- `tests/fixtures/note_japanese.json`
- `tests/fixtures/note_placeholder.pdf`
- `tests/test_notes_ingestion.py`

## Implementation steps

1. Add note parser dataclasses and local decode/hash helpers.
2. Add Markdown/TXT frontmatter/title extraction.
3. Add JSON object parsing for common export shapes.
4. Add PDF placeholder parsing without adding dependencies.
5. Add notes repository insert and duplicate lookup helpers.
6. Wire `pma ingest notes --path/--configured --db --dry-run`.
7. Add artificial Japanese fixtures and tests for Japanese text, frontmatter, JSON, PDF placeholder, duplicate skipping, folder scan, and privacy-safe CLI output.
8. Update docs and run `pytest -q`.

## Tests and verification

Run:

- `pytest -q`

Tests must use artificial fixture files only and temporary SQLite databases. They must not require real notes, network, models, GPU, or optional PDF dependencies.

## Privacy and security

The importer reads only explicitly requested local files. It preserves note bodies in SQLite but never prints body text or file paths in normal CLI output. Source files are treated as read-only and are never modified.

## Performance and hardware

Parsing is CPU-only and streams hashes. No GPU, VRAM, model runtime, or network is used.

## Rollback

Remove the notes ingestion module, CLI wiring, tests, fixtures, and docs additions. No source data is modified by this phase.

## Open questions

None blocking for Phase 1-D.
