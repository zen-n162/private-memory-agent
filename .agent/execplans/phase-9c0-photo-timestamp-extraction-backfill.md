# ExecPlan: Phase 9-C0 Photo Timestamp Extraction And Backfill

## Goal

Add privacy-safe audit and backfill commands for `media_items.taken_at` so temporal event queries can search photos by capture date. The workflow should extract timestamps from existing local media files without modifying source files or printing private paths.

## Non-goals

- Do not ingest new media.
- Do not modify, move, rename, copy, or delete source files.
- Do not print filenames, full paths, GPS, EXIF dumps, captions, LINE text, or note bodies.
- Do not run a full write backfill on real data by default.
- Do not add heavyweight dependencies. Use `exiftool` if installed and Pillow otherwise.

## Current state

- `media_items` has `taken_at`, `modified_at`, and `file_path`, but local validation found all real rows have `taken_at` missing.
- Phase 9-B temporal event queries use `media_items.taken_at` or `modified_at`, so real temporal photo search cannot work reliably when `taken_at` is null.
- `src/private_memory_agent/ingestion/photos.py` can extract EXIF timestamps during fresh ingestion, but there is no post-ingest audit/backfill command.
- SQLite migrations are currently at version 4.
- `pma query` now emits temporal diagnostics, but does not yet recommend timestamp backfill based on missing coverage.

## Proposed design

Add migration v5 to store timestamp provenance on `media_items`:

- `taken_at_source`
- `taken_at_confidence`
- `taken_at_timezone`
- `taken_at_timezone_unknown`
- `metadata_updated_at`

Add `private_memory_agent.media_timestamps` with:

- timestamp audit over existing `media_items`
- timestamp extraction from file metadata using `exiftool`, Pillow, sidecar XMP, filename/path dates, and explicit file mtime fallback
- dry-run/write backfill with `--only-missing`, `--limit`, `--method`, `--fallback`, `--min-confidence`, and safe error summaries
- month histogram based on stored `taken_at`

Add CLI:

```bash
pma media timestamps audit --config configs/paths.local.yaml
pma media timestamps audit --config configs/paths.local.yaml --month-histogram
pma media timestamps backfill --config configs/paths.local.yaml --dry-run --limit 20 --method auto
```

Update temporal diagnostics to include taken-at coverage and a backfill recommendation when missing timestamps dominate.

## Data contracts

Schema migration v5:

```sql
ALTER TABLE media_items ADD COLUMN taken_at_source TEXT;
ALTER TABLE media_items ADD COLUMN taken_at_confidence TEXT;
ALTER TABLE media_items ADD COLUMN taken_at_timezone TEXT;
ALTER TABLE media_items ADD COLUMN taken_at_timezone_unknown INTEGER NOT NULL DEFAULT 1;
ALTER TABLE media_items ADD COLUMN metadata_updated_at TEXT;
CREATE INDEX IF NOT EXISTS idx_media_items_taken_at_source ON media_items(taken_at_source);
```

Dataclasses:

- `TimestampExtraction`: `taken_at`, `source`, `confidence`, `timezone`, `timezone_unknown`, `method`, `error_class`, `safe_message`.
- `TimestampAuditReport`: count-only audit fields and optional month histogram.
- `TimestampBackfillReport`: count-only dry-run/write fields and safe error classes.

Confidence order:

- `high`: EXIF/XMP/video creation timestamps.
- `medium`: clear date in filename/path.
- `low`: file modification time fallback.

## Files to change

- `.agent/execplans/phase-9c0-photo-timestamp-extraction-backfill.md`
- `src/private_memory_agent/storage/migrations.py`
- `src/private_memory_agent/media_timestamps.py`
- `src/private_memory_agent/temporal.py`
- `src/private_memory_agent/cli.py`
- `tests/test_media_timestamps.py`
- `tests/test_temporal_events.py`
- `docs/RETRIEVAL.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add schema migration v5.
2. Implement timestamp extraction helpers with exiftool/Pillow/XMP/filename/file-mtime support.
3. Implement audit and backfill reports with privacy-safe serialization.
4. Add `pma media timestamps audit/backfill` CLI commands.
5. Add timestamp coverage fields to Phase 9-B temporal diagnostics.
6. Add synthetic tests for audit, dry-run, write backfill, fallback, no overwrite, invalid files, CLI safety, and temporal backfill recommendation.
7. Update docs and Japanese overview.
8. Run tests and safe local dry-run commands only.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

If the local DB exists:

```bash
pma media timestamps audit --config configs/paths.local.yaml
pma media timestamps backfill --config configs/paths.local.yaml --dry-run --limit 20 --method auto
```

Do not run a full write backfill unless explicitly requested.

## Privacy and security

All reports are count-only and ID-only. The service reads local source files but never writes them. It never prints filenames, full paths, GPS, EXIF dumps, raw annotation text, LINE text, or note bodies. Tests use synthetic files only.

## Performance and hardware

No GPU or model runtime is required. `exiftool` is used only if installed. Large audits may touch many local files; backfill supports `--limit` and dry-run for safe staged execution.

## Rollback

Revert the CLI/module/docs/test changes. The v5 columns are additive and can remain unused. To fully undo schema changes in a disposable test DB, recreate the SQLite DB from source ingestion.

## Open questions

- Whether to run a full write backfill on the user's real database after reviewing dry-run counts. This should be a separate explicit user action.
