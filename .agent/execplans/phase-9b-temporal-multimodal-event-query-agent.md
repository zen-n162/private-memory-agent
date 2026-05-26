# ExecPlan: Phase 9-B Temporal Multimodal Event Query Agent

## Goal

Support temporal event questions such as `2025年12月で出かけたのはいつ？` by using structured date-range search over photo metadata, photo annotation heuristics, daily clustering, and optional same-day LINE/notes support. The local UI and CLI should show candidate event dates, confidence, privacy-safe evidence IDs, and a clear separation between used, candidate, and rejected evidence.

## Non-goals

- Do not implement face recognition or automatic identity assertions.
- Do not expose filenames, full paths, GPS coordinates, raw LINE text, note bodies, OCR, captions, or raw model output by default.
- Do not mutate original source photos or source files.
- Do not replace the existing general retrieval, E2E, golden, or query pipeline.
- Do not require real models, GPU, network, or private data in unit tests.

## Current state

- `src/private_memory_agent/api/console.py` adapts `/api/chat/query` to `run_e2e_smoke` and returns privacy-safe answer, evidence, trace, and privacy payloads.
- `src/private_memory_agent/api/ui.py` renders a local self-contained console with answer, evidence, trace, privacy, and system status panels.
- `src/private_memory_agent/retrieval/evidence.py` retrieves evidence from text indexes, semantic embeddings, rerankers, and media annotations. General retrieval can return weak photo candidates that are not usable for temporal event questions.
- `media_items` stores `taken_at`, `modified_at`, `media_type`, `mime_type`, dimensions, and private file paths. `media_annotations` stores model annotation text and JSON; `line_messages` and `notes` store timestamped text.
- `pma query` currently routes through the general query flow and does not use a structured temporal event tool.
- `docs/overview_ja.html` must be updated for user-visible retrieval/UI behavior changes.

## Proposed design

Add a small temporal event service that detects obvious temporal outing questions, extracts a deterministic date range, performs read-only photo date-range search, scores outing likelihood from metadata and annotation text, groups candidates by day, and counts same-day LINE/notes support. The service returns a structured `TemporalEventResult` with:

- parsed query metadata
- candidate day clusters
- used/candidate/rejected evidence IDs
- privacy-safe evidence metadata
- a concise answer payload

The local console checks this temporal detector before falling back to the existing E2E path. For temporal results, `/api/chat/query` returns `temporal_event` and evidence roles. The UI renders candidate dates and groups evidence into used, candidate, and rejected sections. The CLI `pma query` also checks temporal questions first and prints a privacy-safe JSON result.

## Data contracts

Internal dataclasses:

- `TemporalDateRange(start: date, end: date)` with end exclusive.
- `TemporalEventQuery(query_type, date_range, event_type, preferred_sources, primary_tool)`.
- `TemporalEvidenceItem(evidence_id, source_type, should_use, evidence_role, specificity, relevance_score, reason_category, occurred_at)`.
- `PhotoCandidate(media_item_id, evidence_id, taken_at, day, media_type, has_annotation, has_location, annotation_available, outing_score, reasons, should_use)`.
- `DailyEventCluster(date, photo_count, annotated_photo_count, outing_score, confidence, top_evidence_ids, candidate_evidence_ids, rejected_evidence_ids, line_support_count, notes_support_count, support_evidence_ids, reason)`.
- `TemporalEventResult(query, candidate_dates, used_evidence_ids, candidate_evidence_ids, rejected_evidence_ids, answer, evidence, diagnostics, warnings)`.

API additions:

- Existing `/api/chat/query` response may include `temporal_event`.
- Evidence items may include `evidence_role` with `used`, `candidate`, or `rejected`.

CLI:

- `pma query "<question>" --config ...` detects temporal event queries and prints privacy-safe JSON.

## Files to change

- Create `src/private_memory_agent/temporal.py`.
- Update `src/private_memory_agent/api/console.py`.
- Update `src/private_memory_agent/api/ui.py`.
- Update `src/private_memory_agent/cli.py`.
- Add tests, likely `tests/test_temporal_events.py`, plus console/UI assertions in `tests/test_api_console.py`.
- Update `docs/RETRIEVAL.md`.
- Update `docs/ROADMAP.md`.
- Update `docs/overview_ja.html`.

## Implementation steps

1. Implement deterministic temporal query parsing for month expressions and outing intent.
2. Implement read-only SQLite photo date-range search using `media_items.taken_at` with safe metadata only.
3. Implement outing likelihood scoring and daily clustering.
4. Implement same-day LINE/notes support counts and IDs without returning raw text.
5. Implement `answer_temporal_event_query` returning privacy-safe structured results.
6. Integrate temporal handling into the local console before the E2E fallback.
7. Update UI rendering for temporal candidate dates and used/candidate/rejected evidence groups.
8. Integrate temporal handling into `pma query`.
9. Add synthetic tests for parsing, date search, scoring, clustering, support counts, API output, and privacy.
10. Update documentation and Japanese overview.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

If the local DB exists, run a privacy-safe manual check:

```bash
pma query "2025年12月で出かけたのはいつ？" --config configs/paths.local.yaml
```

No test may require private data, real models, GPU, Docker, or network.

## Privacy and security

The temporal service only reads local SQLite metadata and never modifies source files. Default output includes IDs, dates, counts, scores, and reason categories only. It does not print filenames, full paths, GPS coordinates, raw LINE text, note bodies, OCR, full captions, or raw model output. Evidence with `should_use=false` is marked as rejected/candidate and is not counted as answer evidence.

## Performance and hardware

No GPU is required. The date-range photo query uses indexed `media_items.taken_at`; annotation joins are limited by the date range and result limit. LINE/notes support checks are simple indexed date filters. Large real photo collections should remain manageable because this path is structured by date instead of broad vector search.

## Rollback

Remove `src/private_memory_agent/temporal.py`, revert console/UI/CLI integrations, remove tests and docs updates. Existing retrieval, E2E, golden, ingestion, and model runtime code remains independent and should continue to work.

## Open questions

- Whether future phases should use confirmed home/work locations to improve outing scoring. This phase only uses safe metadata booleans and annotation keywords.
