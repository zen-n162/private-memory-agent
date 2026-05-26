# ExecPlan: Phase 9-C0 Debug Timestamp Backfill Write Path

## Goal

Make timestamp backfill writes update the intended SQLite database and add
tests that prove the dry-run count, extraction, confidence filtering, SQL
update, transaction commit, provenance fields, and `only_missing` behavior work.

## Non-goals

- Do not modify source photos/videos.
- Do not print filenames, full paths, EXIF payloads, GPS, captions, OCR, LINE
  text, note bodies, or personal names.
- Do not run a large real-data backfill automatically.
- Do not change timestamp extraction priority.

## Current state

- The timestamp backfill service writes and commits when `dry_run=False`.
- The CLI now has `--apply`, but timestamp audit/backfill still default to a
  hard-coded `DEFAULT_E2E_DB_PATH` parser default.
- `configs/paths.example.yaml` contains `storage.sqlite_path`, but the config
  loader does not expose it and timestamp commands do not resolve DB path from
  `configs/paths.local.yaml`.
- A dry-run may therefore report update candidates from one database while the
  user checks counts in another database.

## Proposed design

- Extend `PathSettings` with `sqlite_path`.
- Load `storage.sqlite_path` from paths config, defaulting to
  `app_data_dir/private_memory_agent.sqlite3`.
- For `pma media timestamps audit/backfill`, set `--db` default to `None` and
  resolve to:
  1. explicit `--db`, if provided
  2. configured `storage.sqlite_path`
- Add `--commit-interval` for apply mode so long backfills periodically commit
  SQLite metadata updates instead of making progress visible only at process
  exit.
- Keep output privacy-safe. Do not print resolved paths by default.
- Keep `--apply` as the explicit write flag and `--write` as an alias.

## Data contracts

`ConfigBundle.paths.sqlite_path` is a `Path` representing the configured local
SQLite database.

No schema changes are needed. Existing backfill writes:

- `media_items.taken_at`
- `media_items.taken_at_source`
- `media_items.taken_at_confidence`
- `media_items.taken_at_timezone`
- `media_items.taken_at_timezone_unknown`
- `media_items.metadata_updated_at`
- `media_items.updated_at`

## Files to change

- `.agent/execplans/phase-9c0-debug-timestamp-backfill-write-path.md`
- `src/private_memory_agent/config/loader.py`
- `src/private_memory_agent/cli.py`
- `tests/test_config_loader.py`
- `tests/test_media_timestamps.py`
- `docs/RETRIEVAL.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add `sqlite_path` to config loading and safe config dict output.
2. Resolve timestamp command DB path from explicit `--db` or config.
3. Make UPDATE rowcount drive `updated_count`.
4. Add periodic apply-mode commits via `--commit-interval`.
5. Add tests that CLI dry-run uses configured DB and does not write.
6. Add tests that CLI `--apply` writes and commits to configured DB, storing
   source/confidence provenance.
7. Add tests that explicit `--db` overrides config and `--only-missing`
   preserves existing timestamps.
8. Update docs and overview to clarify DB resolution and verification.
9. Run full test and HTML validation.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
pma media timestamps backfill --help
```

Safe local checks:

```bash
pma media timestamps backfill --config configs/paths.local.yaml --dry-run --limit 20 --method auto
```

Only after a DB backup:

```bash
pma media timestamps backfill --config configs/paths.local.yaml --limit 20 --method auto --only-missing --apply
sqlite3 data/local/private_memory_agent.sqlite3 "SELECT COUNT(*) FROM media_items WHERE taken_at IS NOT NULL AND taken_at != '';"
```

## Privacy and security

The diagnostics remain count/ID only. Tests use synthetic media files in
temporary directories. Source files are opened read-only for metadata extraction
and never changed.

## Performance and hardware

No GPU, model server, network, or Docker is required. Real backfill cost depends
on media count and extraction method.

## Rollback

Revert the config loader path addition, CLI DB resolution change, tests, docs,
and plan. Existing explicit `--db` behavior would remain available.

## Open questions

None blocking.
