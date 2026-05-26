# ExecPlan: Phase 9-C Temporal Coverage Diagnostics And Fallback Search

## Goal

Make temporal event answers diagnostically useful when they return unknown. For questions such as `2025年12月で出かけたのはいつ？`, expose the parsed date range, photo timestamp coverage, date-range query stages, nearby month counts, and safe LINE/notes fallback support evidence.

## Non-goals

- Do not hallucinate outing dates when evidence is missing or weak.
- Do not print filenames, full paths, GPS, EXIF dumps, raw LINE messages, note bodies, OCR, or photo captions.
- Do not modify source media files or run timestamp backfill writes.
- Do not replace the existing temporal event flow or general retrieval flow.
- Do not require model calls, GPU, network, or private data in tests.

## Current state

- Phase 9-B detects obvious temporal outing questions and searches photos by date, scores outing likelihood, clusters by day, and returns candidate dates.
- Phase 9-C0 added media timestamp audit/backfill and temporal diagnostics now include basic `taken_at` coverage.
- In the real local DB, all `media_items.taken_at` values are missing, so temporal photo queries return no photo candidates and only say no photos were found.
- The UI displays answer, evidence, and trace panels but does not yet expose detailed temporal diagnostics.

## Proposed design

Extend `private_memory_agent.temporal` with a temporal coverage diagnostics layer:

- `TemporalDateRange` records `source`, `expression`, and `timezone`.
- Photo date-range search uses capture-date `media_items.taken_at` as the query column and reports stage counts.
- A coverage helper reports media timestamp coverage, nearby previous/current/next month counts, and filter removal counts.
- If photo evidence is missing or weak, run same-range LINE/notes fallback search for configurable outing/event terms and return counts plus safe evidence IDs.
- The temporal answer should distinguish:
  - no photos and no text support
  - no photos but LINE/notes support possible outing dates
  - photos present but weak

The local console will put these diagnostics into `trace.temporal_diagnostics`. The UI will render them in Agent Trace. `pma query` will accept `--temporal-diagnostics`; temporal query JSON remains privacy-safe and includes the diagnostics.

## Data contracts

Add/extend fields:

- `TemporalDateRange.to_dict()`:
  - `start`
  - `end`
  - `label`
  - `source`
  - `expression`
  - `timezone`
  - `end_exclusive`
- `TemporalEventResult.diagnostics`:
  - `parsed_date_range`
  - `parsed_date_range_start`
  - `parsed_date_range_end`
  - `date_range_source`
  - `parsed_temporal_expression`
  - `timezone`
  - `date_range_query_column`
  - `date_range_query_status`
  - `photo_candidates_count`
  - `annotated_photo_candidates_count`
  - `unannotated_photo_candidates_count`
  - `candidates_before_media_type_filter`
  - `candidates_after_media_type_filter`
  - `candidates_before_annotation_filter`
  - `candidates_after_annotation_filter`
  - `removed_reason_counts`
  - `nearby_month_counts`
  - `line_date_support_count`
  - `notes_date_support_count`
  - `support_evidence_ids`
  - `fallback_sources_used`

Evidence IDs remain stable and safe: `media_items:<id>`, `line_messages:<id>`, `notes:<id>`.

## Files to change

- `.agent/execplans/phase-9c-temporal-coverage-diagnostics-fallback.md`
- `src/private_memory_agent/temporal.py`
- `src/private_memory_agent/api/console.py`
- `src/private_memory_agent/api/ui.py`
- `src/private_memory_agent/cli.py`
- `tests/test_temporal_events.py`
- `tests/test_api_console.py`
- `docs/RETRIEVAL.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Extend date range parsing metadata.
2. Add photo coverage and filter-stage diagnostics.
3. Add nearby month counts around the parsed range.
4. Add same-range LINE/notes fallback search with safe support evidence IDs.
5. Update temporal answer logic for photo-missing/text-support and all-sources-missing cases.
6. Add `--temporal-diagnostics` to `pma query`.
7. Add UI rendering for temporal diagnostics in Agent Trace.
8. Add synthetic tests for all required branches and privacy safety.
9. Update documentation and Japanese overview.
10. Run full verification and safe local temporal diagnostic command.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

If the local DB exists:

```bash
pma query "2025年12月で出かけたのはいつ？" \
  --config configs/paths.local.yaml \
  --temporal-diagnostics
```

No test may require private data, real models, GPU, Docker, or network.

## Privacy and security

Only counts, date ranges, reason categories, and evidence IDs are returned. No filenames, full paths, GPS, EXIF dumps, raw LINE messages, note bodies, OCR, or photo captions are printed. Source files are never modified.

## Performance and hardware

No GPU is required. Diagnostics use indexed timestamp columns and limited fallback support queries. Nearby month counts are aggregate SQL only.

## Rollback

Revert temporal, UI, CLI, tests, and docs changes. Phase 9-B and Phase 9-C0 remain usable without the additional diagnostic fields.

## Open questions

- Whether future phases should allow user-configurable temporal fallback terms through YAML. This phase keeps a safe built-in term list but structures the code so config can be added later.
