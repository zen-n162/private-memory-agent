# ExecPlan: Phase 8-B Annotation Batch Audit and Stats

## Goal

Add privacy-safe commands for inspecting annotation batch progress and failures
without exposing filenames, full paths, image contents, EXIF/GPS, OCR text, or
private source data.

Required commands:

- `pma stats --config configs/paths.local.yaml`
- `pma annotate photos --status --config configs/paths.local.yaml`
- `pma annotate photos --failed --config configs/paths.local.yaml`

## Non-goals

- Do not run annotation batches.
- Do not inspect private source directories.
- Do not print filenames, paths, GPS, EXIF, OCR text, captions, LINE text,
  note bodies, or personal names.
- Do not add a new database table unless existing storage cannot support the
  audit requirement.

## Current state

Photo annotations are stored in `media_annotations`. Active image media items
are stored in `media_items`. The existing `audit_log` table can hold
privacy-safe operational events, but photo annotation failures are not currently
persisted there. The CLI has `annotate photos` for processing but no status or
failure inspection commands.

## Proposed design

Add a lightweight annotation stats service that reads only aggregate and id
data from SQLite:

- active `media_items` count
- active image media count
- active `media_annotations` count
- distinct annotated image media count
- unannotated image media count
- inferred unsupported image format count
- tracked failed annotation count from `audit_log`
- model id breakdown
- latest annotation timestamp
- success rate

Persist per-item annotation failures as safe `audit_log` entries with:

- media item id
- error class
- safe message
- safe image format
- safe dimensions
- preprocessing success flag
- model id

Expose JSON output for both status and failed lists, because JSON is explicit,
easy to test, and less likely to accidentally include extra private context.

## Data contracts

Audit action for failures:

```text
action = "photo_annotation.error"
target_table = "media_items"
target_id = <media item id>
status = "error"
detail_json = {
  "error_class": "...",
  "message": "...",
  "image_format": "...",
  "dimensions": "WxH",
  "preprocessing_succeeded": true|false|null,
  "model_id": "..."
}
```

Stats output is a JSON object with a top-level `photo_annotations` object.
Failed output is a JSON object with `failed_annotations`, containing safe IDs
and error metadata only.

## Files to change

- `src/private_memory_agent/annotation/photos.py`
- `src/private_memory_agent/annotation/stats.py`
- `src/private_memory_agent/annotation/__init__.py`
- `src/private_memory_agent/cli.py`
- `tests/test_photo_annotation.py`
- `docs/MODEL_RUNTIME.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add annotation stats dataclasses and SQLite query helpers.
2. Log privacy-safe photo annotation failures to `audit_log`.
3. Add `pma stats` top-level command.
4. Add `pma annotate photos --status` and `--failed` modes that do not
   initialize model clients or call endpoints.
5. Add synthetic tests for aggregate counts, model breakdown, failure logging,
   failed list output, and privacy-safe CLI output.
6. Update runtime docs and Japanese overview command list.
7. Run `python -m pytest -q`.

## Tests and verification

Run:

```bash
python -m pytest -q
```

Optional manual commands:

```bash
pma stats --config configs/paths.local.yaml
pma annotate photos --status --config configs/paths.local.yaml
pma annotate photos --failed --config configs/paths.local.yaml
```

These commands read local SQLite metadata only and should not inspect source
files or start model calls.

## Privacy and security

The new commands print aggregate counts, model IDs, timestamps, and media item
IDs only. Failure details come from sanitized errors already designed not to
include paths or private content. The stats service does not open source files
or inspect private directories.

## Performance and hardware

No GPU, model, or endpoint dependency. SQLite aggregate queries are small and
read-only.

## Rollback

Remove the stats module and CLI modes, and stop writing photo annotation errors
to `audit_log`. Existing safe audit rows can remain harmlessly or be ignored.

## Open questions

None blocking.
