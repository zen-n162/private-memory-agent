# ExecPlan: Phase 9-F Temporal Query Chunking And Candidate Pruning

## Goal

Make broad temporal outing queries such as `2025年夏で出かけたのはいつ？`,
`去年の夏で出かけた日は？`, and `2025年に出かけた日は？` use the temporal
event agent instead of falling back to broad model/retrieval paths. Broad ranges
should be split into chunks, candidate days should be ranked and pruned, and the
UI should still show candidate dates and diagnostics even when model answer
generation is unavailable.

## Non-goals

- Do not run real model batches by default.
- Do not change photo annotation, ingestion, or timestamp backfill behavior.
- Do not show filenames, paths, GPS, EXIF, raw LINE text, note bodies, OCR, or
  full captions.
- Do not introduce external services or GPU requirements.

## Current state

- `src/private_memory_agent/temporal.py` parses month expressions like
  `2025年12月`, `2025/12`, relative month phrases, and `去年の夏`.
- The local chat console checks temporal queries before the normal E2E path.
- Narrow month queries work and render candidate dates.
- `2025年夏` and full-year phrases are not reliably parsed as temporal event
  queries, so the UI can fall through to broad retrieval/model flow and return a
  `ModelRuntimeError`.
- Temporal diagnostics currently include date range, `taken_at` coverage,
  candidate counts, and fallback counts, but not chunking/pruning diagnostics.

## Proposed design

Extend deterministic temporal parsing for:

- `YYYY年夏`
- `YYYY年春`
- `YYYY年秋`
- `YYYY年冬`
- `YYYY年`

Add a temporal chunking layer:

- If the parsed range is longer than 45 days, chunk by month.
- If the range is longer than 180 days, still chunk by month and cap candidates
  per month.
- For each chunk, run the same photo date-range search and daily clustering.
- Merge and deduplicate daily clusters across chunks.

Add candidate pruning:

- Rank candidate days by confidence, outing score, support counts, then date.
- Keep `top_candidate_dates` candidate days, default 10.
- Keep `top_evidence_per_date` evidence IDs per role/date, default 5.
- Return diagnostics for candidates before/after pruning and evidence count.

The current temporal answer path is deterministic and does not call a leader
model. That is acceptable for this phase: broad temporal queries should return
candidate dates and structured diagnostics without invoking a large model. The
real-model UI mode can still display this temporal result safely.

## Data contracts

`TemporalEventResult.diagnostics` adds:

- `original_date_range`
- `date_range_days`
- `chunking_enabled`
- `chunk_count`
- `chunk_size`
- `chunks`
- `candidates_before_pruning`
- `candidates_after_pruning`
- `top_candidate_dates`
- `top_evidence_per_date`
- `evidence_sent_count`
- `pruning_reason`

`answer_temporal_event_query()` adds optional keyword parameters:

- `top_candidate_dates: int = 10`
- `top_evidence_per_date: int = 5`
- `chunk_after_days: int = 45`
- `long_range_days: int = 180`
- `candidates_per_long_range_chunk: int = 5`

## Files to change

- `src/private_memory_agent/temporal.py`
- `src/private_memory_agent/api/console.py` if temporal options need passing
- `src/private_memory_agent/api/ui.py` for chunk/pruning diagnostics display
- `tests/test_temporal_events.py`
- `docs/RETRIEVAL.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Extend date parsing for explicit year-season and full-year expressions.
2. Add month chunk creation helpers for long ranges.
3. Run photo search and clustering per chunk when needed.
4. Merge/dedupe candidate clusters and prune candidate dates/evidence IDs.
5. Add broad temporal diagnostics.
6. Update UI temporal diagnostics to show chunking/pruning fields.
7. Add synthetic tests for narrow/no chunk, summer chunking, full-year parsing,
   pruning, and privacy-safe output.
8. Update documentation and the Japanese overview.
9. Run full verification.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

Synthetic tests must cover:

- `2025年12月` is not chunked.
- `2025年夏` is parsed as June 1 to September 1 and chunked by month.
- Candidate days from multiple chunks are merged.
- Candidate days are pruned to `top_candidate_dates`.
- Evidence IDs per date are pruned to `top_evidence_per_date`.
- The temporal chat payload contains candidate dates and does not fall back to
  model error for broad ranges.
- No private paths or raw text appear in output.

## Privacy and security

All processing remains read-only against SQLite and source media files. Output
contains only dates, counts, safe evidence IDs, confidence values, reason codes,
and path-free thumbnail URLs. Snippets remain governed by existing explicit UI
controls.

## Performance and hardware

No GPU is required. Chunking reduces peak candidate volume for broad date
ranges. Default pruning keeps at most 10 candidate days and 5 evidence IDs per
date, which bounds UI payload and prompt-like summaries.

## Rollback

Revert temporal parser/chunking/pruning changes, UI diagnostic additions,
tests, docs, and this ExecPlan. Narrow month temporal queries will continue to
work as before.

## Open questions

None blocking.
