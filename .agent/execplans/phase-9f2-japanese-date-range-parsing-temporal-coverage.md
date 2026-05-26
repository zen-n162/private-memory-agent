# ExecPlan: Phase 9-F2 Japanese Date-Range Parsing and Temporal Coverage

## Goal

Support Japanese month range expressions such as `2025年10月から12月` for temporal
outing queries, and expose privacy-safe diagnostics that make it clear which
range was parsed, which months were searched, and how pruning affected final
candidate months.

## Non-goals

- Do not add new model inference behavior.
- Do not inspect, ingest, or modify private source files.
- Do not print filenames, full paths, GPS, EXIF, raw LINE text, note bodies, or
  photo captions.
- Do not tune outing quality beyond the existing temporal scorer.

## Current state

`src/private_memory_agent/temporal.py` handles single months, seasons, full
years, and broad-range chunking/pruning. It does not yet parse Japanese month
ranges like `2025年10月から12月`, so such queries can be reduced to only the first
month. Existing diagnostics include parsed range, chunk counts, pruning counts,
photo coverage, and nearby month counts. UI temporal diagnostics are rendered in
`src/private_memory_agent/api/ui.py`.

## Proposed design

Add a range parser before single-month parsing. It will detect:

- `YYYY年M月からN月`
- `YYYY年M月 からN月`
- `YYYY年M月〜N月`
- `YYYY年M月からYYYY年N月`
- `YYYY年M月からYYYY年N月まで`
- `YYYY年M月以降N月まで`
- `M月からN月` when one clear year exists nearby in the query

The parsed range is inclusive by start month and exclusive after the end month.
For example, `2025年10月から12月` becomes `2025-10-01` to `2026-01-01`.

Add count-only month diagnostics:

- `date_range_confidence`
- `date_range_parse_warnings`
- `months_covered`
- `photo_count_by_month`
- `candidate_date_count_by_month`
- `line_support_count_by_month`
- `notes_support_count_by_month`
- `pruned_months`
- `top_candidate_date_limit`

When final candidate dates cover fewer months than the parsed range, add a
privacy-safe warning explaining which months were missing from final candidates.

## Data contracts

`TemporalDateRange.to_dict()` will include:

- `confidence`
- `parse_warnings`

Temporal result diagnostics will include:

- `date_range_confidence`
- `date_range_parse_warnings`
- month coverage and pruning fields listed above

All fields are counts, ISO dates, month keys, booleans, or evidence IDs only.

## Files to change

- `src/private_memory_agent/temporal.py`
- `src/private_memory_agent/api/ui.py`
- `tests/test_temporal_events.py`
- `docs/RETRIEVAL.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Extend `TemporalDateRange` with confidence and parse warnings.
2. Add Japanese month range parser before single-month parser.
3. Add month list/count helpers and pruning month diagnostics.
4. Add month coverage fields to temporal diagnostics and UI rendering.
5. Add synthetic tests for date-range parsing, diagnostics, and pruning warning.
6. Update docs and overview.
7. Run verification commands.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

If the local DB exists, run:

```bash
pma query "2025年10月から12月で出かけたのはいつ？" \
  --config configs/paths.local.yaml \
  --temporal-diagnostics
```

Verify the parsed range is `2025-10-01` to `2026-01-01`, month coverage includes
October, November, and December, and no private content is printed.

## Privacy and security

Diagnostics are count-only and ID-only. The change must not expose paths,
filenames, raw messages, note bodies, captions, GPS, EXIF, OCR, or model output.
Tests use synthetic data only.

## Performance and hardware

No GPU or model runtime is required. Month count queries are small SQLite
aggregations over the existing date range and should be cheaper than evidence
generation.

## Rollback

Revert the parser and diagnostics changes in `temporal.py`, remove the UI
diagnostic rows, and remove the tests/docs added in this phase.

## Open questions

None blocking.
