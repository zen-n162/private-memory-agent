# Phase 9-I: Open-ended Temporal Event Query Planning

## Goal

Support temporal event questions that ask "when" without an explicit date
range, such as `ラーメンを食べに行っているのはいつ？`. These should enter the
temporal event workflow, infer an event intent/subtype, search available local
memory with safe caps, extract candidate dates from dated evidence, and return
candidate dates or a structured insufficient-evidence answer instead of a
generic model runtime failure.

## Current Problem

Date-bounded temporal event queries work. Open-ended temporal questions can
retrieve a small amount of event-related evidence through the general path, but
do not convert dated evidence into candidate dates because the temporal event
workflow expects a parsed date range. Real-model mode may then attempt answer
synthesis with an invalid temporal payload and surface `ModelRuntimeError`.

## Approach

1. Extend temporal query parsing:
   - classify "いつ" event questions as `temporal_event_search` even without a
     deterministic date range.
   - report `date_range_status=unspecified`.
   - infer a bounded all-available-memory range from DB coverage.
2. Extend `EventIntentPlan`:
   - add `event_subtype`.
   - let deterministic fallback infer open-vocabulary subtypes such as `ramen`.
   - keep Leader planner JSON compatible by accepting missing subtype.
3. Add open-ended search diagnostics:
   - `open_ended_temporal_query`
   - `date_scope_strategy`
   - inferred search range
   - chunk counts
   - dated/undated evidence counts
   - candidate counts by month
4. Search photos, LINE, and notes across the inferred range using
   event-specific signals, then group dated evidence by day.
5. If evidence exists but no dated candidates can be produced, return a
   structured unknown/insufficient result with safe diagnostics.
6. Keep date-bounded temporal behavior intact.
7. Update tests, docs, and the Japanese overview.

## Privacy

Default outputs and diagnostics must remain count/ID/status based. Do not print
raw LINE text, note bodies, photo captions, filenames, paths, GPS, EXIF, OCR,
raw prompts, or raw model output.

## Verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

Optional local checks after restarting the API:

```bash
pma query "ラーメンを食べに行っているのはいつ？" --config configs/paths.local.yaml --temporal-diagnostics
pma query "2025年12月で、ご飯を食べに行っているのはいつ？" --config configs/paths.local.yaml --temporal-diagnostics
```
