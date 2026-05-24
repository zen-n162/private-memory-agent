# ExecPlan: Phase 1-B Photo Ingestion Without AI

## Goal

Add a local, read-only photo metadata ingestion command: `pma ingest photos --path <folder>`. The command should recursively scan supported media files, compute SHA256, extract available safe metadata, and store rows in `source_items` and `media_items` without AI model inference.

## Non-goals

- Do not run vision models, OCR, captioning, face recognition, embeddings, or API calls.
- Do not create thumbnails unless a later phase explicitly requests them.
- Do not ingest real photos in tests.
- Do not modify, move, rename, delete, or write beside source files.
- Do not log real filenames, EXIF payloads, GPS coordinates, or private directory listings by default.
- Do not implement vector DB or retrieval.

## Current state

Phase 1-A added SQLite storage with migrations and repositories for `source_items` and `media_items`. The CLI has `doctor`, `config show`, and `models list`, but no ingestion commands. Config supports local raw source paths, and real paths are checked only by explicit commands. Tests use temporary paths and synthetic data only.

## Proposed design

Add an ingestion package with a photo scanner and importer:

- Supported extensions: `.jpg`, `.jpeg`, `.png`, `.webp`, `.heic`, `.mov`, `.mp4`.
- SHA256 is computed by streaming file bytes.
- Image width/height is extracted with standard-library parsers for PNG and JPEG. If Pillow is already installed, use it opportunistically for width/height, EXIF taken time, and GPS metadata. If HEIC support is not available, HEIC files are still tracked with file/hash metadata.
- MOV/MP4 receive metadata placeholders: file path, size, modified time, SHA256, media type `video`.
- Dry-run mode scans and hashes but does not open or write a database.
- Import mode initializes SQLite and writes one `source_items` row and one `media_items` row per new file.
- Duplicate policy: skip when either source path already exists as a source item or content SHA256 already exists for the same source type/media hash.

CLI:

- `pma ingest photos --path <folder>`
- `--dry-run`
- `--db <sqlite-path>` optional, defaulting to `data/local/private_memory_agent.sqlite3`

Output is summary-only: counts by scanned/imported/skipped/errors. It does not print file names or private metadata.

## Data contracts

`PhotoMetadata`:

- `path: Path`
- `sha256: str`
- `file_size_bytes: int`
- `modified_at: str`
- `media_type: image | video`
- `mime_type: str | None`
- `width: int | None`
- `height: int | None`
- `taken_at: str | None`
- `gps_json: str | None`
- `metadata_json: str`

Storage rows:

- `source_items.source_type = "photo"`
- `source_items.source_uri = absolute file path`
- `source_items.content_sha256 = sha256`
- `source_items.metadata_json` contains safe importer/source metadata.
- `media_items` stores media metadata and `metadata_json` containing GPS if available.

## Files to change

- `.agent/execplans/phase-1b-photo-ingestion-no-ai.md`
- `src/private_memory_agent/cli.py`
- `src/private_memory_agent/ingestion/__init__.py`
- `src/private_memory_agent/ingestion/photos.py`
- `src/private_memory_agent/storage/repositories.py`
- `tests/fixtures/tiny.png`
- `tests/test_photo_ingestion.py`
- `tests/test_cli.py`

## Implementation steps

1. Add scanner/importer dataclasses and metadata extraction helpers.
2. Add duplicate lookup helpers to source/media repositories.
3. Add `pma ingest photos` CLI wiring with `--path`, `--db`, and `--dry-run`.
4. Add tiny synthetic image fixture under `tests/fixtures`.
5. Add tests for dry-run, import, duplicate skipping, unsupported files, and privacy-safe output.
6. Run `pytest -q`.
7. Run a dry-run smoke command against the synthetic fixture directory.

## Tests and verification

Run:

- `pytest -q`
- `pma ingest photos --path tests/fixtures --dry-run`

Tests must use synthetic fixture images only. They must not require real photos, local raw source paths, GPU, models, network, or optional image libraries.

## Privacy and security

The importer reads source files only to compute hashes and metadata. It never writes to source directories. CLI output is count-based and does not include filenames, raw EXIF dumps, GPS values, or personal metadata. Tests assert that fixture filenames are not printed by default.

## Performance and hardware

The scanner streams files for hashing and uses lightweight metadata parsing. It is CPU-only and does not allocate GPU/VRAM.

## Rollback

Remove the ingestion package, CLI command wiring, tests, and fixture. The storage schema remains compatible and no real source data is modified.

## Open questions

None blocking for Phase 1-B.
