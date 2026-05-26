# ExecPlan: Phase 9-C0 Timestamp Backfill Apply Mode

## Goal

Make media timestamp backfill execution explicit and understandable. A user who
runs `pma media timestamps backfill` without an apply flag should clearly see
that it is a dry run, and a user who passes `--apply` should update SQLite
`media_items.taken_at` metadata while leaving original source media read-only.

## Non-goals

- Do not modify, move, rename, or rewrite source photos/videos.
- Do not print filenames, full paths, GPS, EXIF dumps, note bodies, LINE text, or
  captions.
- Do not run a full real-data write backfill automatically.
- Do not change timestamp extraction priority or add heavy dependencies.

## Current state

- `backfill_media_timestamps(..., dry_run=True)` defaults to dry-run and writes
  only when `dry_run=False`.
- The CLI currently sets `--dry-run` as default and provides `--write` for real
  DB writes.
- The user ran the command without `--dry-run`, expected a write, and got
  `dry_run=True`, `updated_count=0`, and nonzero `dry_run_update_count`.
- Documentation mentions dry-run and `--write`, but the execution path is not
  clear enough.

## Proposed design

Keep the safer default dry-run behavior, but make the write action explicit:

- Add `--apply` as the preferred flag for real SQLite metadata updates.
- Keep `--write` as a backward-compatible alias for `--apply`.
- Update help text to state that dry-run is default and source files are never
  modified.
- Add mode messages:
  - dry-run: `DRY RUN: no database rows were updated. Re-run with --apply to write timestamps.`
  - apply: `APPLY MODE: database metadata was updated. Source files were not modified.`
- Include the same message in JSON reports as `mode_message`.

## Data contracts

`TimestampBackfillReport.to_dict()` gains:

- `mode_message`: a privacy-safe string describing dry-run/apply behavior.

No database schema change is needed. Existing timestamp columns are used:

- `taken_at`
- `taken_at_source`
- `taken_at_confidence`
- `taken_at_timezone`
- `taken_at_timezone_unknown`
- `metadata_updated_at`

## Files to change

- `.agent/execplans/phase-9c0-fix-timestamp-backfill-apply-mode.md`
- `src/private_memory_agent/cli.py`
- `src/private_memory_agent/media_timestamps.py`
- `tests/test_media_timestamps.py`
- `docs/RETRIEVAL.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add `--apply` to the timestamp backfill CLI mutually exclusive dry-run/write
   group, keeping `--write` as an alias.
2. Improve CLI help text for dry-run/apply behavior.
3. Add privacy-safe mode messages to formatted and JSON backfill reports.
4. Add synthetic tests for default dry-run, `--apply` writes, `--dry-run` no
   writes, existing timestamp preservation, committed updates, source file mtime
   preservation, and privacy-safe output.
5. Update retrieval docs, roadmap, and Japanese overview.
6. Run full tests and HTML validation.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
pma media timestamps backfill --help
```

If the local DB exists, run only a safe dry-run:

```bash
pma media timestamps backfill \
  --config configs/paths.local.yaml \
  --dry-run \
  --limit 20 \
  --method auto
```

Do not run a real local DB `--apply` in automation unless the DB has been backed
up and the user explicitly wants it.

## Privacy and security

The change only updates SQLite metadata when `--apply` is explicit. It never
prints source file paths or metadata payloads. Test fixtures are synthetic.

## Performance and hardware

No GPU, model server, network, Docker, or private data is required. Real local
backfill cost depends on media count and extraction method.

## Rollback

Revert the CLI help/flag addition, report message addition, tests, and docs.
The existing `--write` behavior would remain as before if the CLI patch is
reverted.

## Open questions

None blocking.
