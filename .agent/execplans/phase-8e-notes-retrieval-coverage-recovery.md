# ExecPlan: Phase 8-E Notes Retrieval Coverage Recovery

## Goal

Recover note evidence in no-fallback E2E smoke and retrieval audit while keeping
privacy-safe output. Diagnostics should show whether notes are indexed, matched,
converted to evidence, and retained after ranking.

## Non-goals

- Do not expose note titles or bodies in normal diagnostics.
- Do not ingest or modify source notes.
- Do not change model runtime behavior.
- Do not require GPU, network, model servers, Qdrant, or real private data in
  tests.
- Do not degrade existing photo or LINE retrieval.

## Current state

Phase 8-D made local smoke return real photo and LINE evidence without inventory
fallback. However, source coverage showed `real_note_evidence_count=0` even
though the text index contains `notes=878`.

Current retrieval calls text search with a single small limit. The index is
filled in source order: LINE rows first, then notes, then media annotations.
For broad Japanese keyword fallback, early LINE rows can fill the candidate
window before note rows are seen.

## Proposed design

- Keep canonical public source names: `photos`, `line`, and `notes`.
- Add source-table filtering to text search and text-search diagnostics.
- Have `RetrievalService` query each requested source table separately, then
  merge/rank/dedupe as before.
- Add source-specific diagnostics for text candidates, FTS candidates, LIKE
  fallback candidates, post-filter candidates, final evidence counts, and drop
  reasons.
- Add `pma e2e smoke --require-source notes` to fail/warn when a required
  source is available but absent from retrieved evidence.
- Add note-friendly generic example smoke queries.
- Make the fake leader model conservative for weak LINE/note evidence so E2E
  fake-model validation checks retrieval coverage rather than failing on
  overconfident synthetic answers.

## Data contracts

Extend diagnostics output with:

- `source_stage_counts`: map keyed by `photos`, `line`, `notes`
  - `fts_candidate_count`
  - `exact_like_candidate_count`
  - `keyword_like_candidate_count`
  - `text_candidate_count`
  - `candidate_count_after_source_filter`
  - `candidate_count_after_ranking`
  - `evidence_conversion_count`
  - `drop_reason`

Extend E2E options/report:

- `require_sources`: tuple of required public source names
- warnings when required sources are available but absent

## Files to change

- `.agent/execplans/phase-8e-notes-retrieval-coverage-recovery.md`
- `src/private_memory_agent/retrieval/text.py`
- `src/private_memory_agent/retrieval/evidence.py`
- `src/private_memory_agent/db_diagnostics.py`
- `src/private_memory_agent/e2e.py`
- `src/private_memory_agent/cli.py`
- `src/private_memory_agent/agent/leader.py`
- `configs/e2e_smoke_queries.example.yaml`
- `tests/test_text_retrieval.py`
- `tests/test_evidence_retrieval.py`
- `tests/test_db_diagnostics.py`
- `tests/test_e2e_smoke.py`
- `tests/test_leader_agent.py`
- `docs/RETRIEVAL.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add optional `source_tables` filtering to text search and diagnostics.
2. Query text search per requested source table in `RetrievalService`.
3. Add note/source stage diagnostics in retrieval audit and E2E output.
4. Add `--require-source` to E2E smoke.
5. Add synthetic tests for note exact, Japanese fallback, mixed-source recovery,
   E2E source coverage, and privacy-safe output.
6. Fix fake-model answer generation for line/note and mixed evidence.
7. Update docs and example smoke queries.
8. Run unit tests, HTML check, and local smoke commands.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
pma index text --config configs/paths.local.yaml
pma e2e smoke --config configs/paths.local.yaml --retrieval-only --diagnose --no-fallback --json
pma e2e smoke --config configs/paths.local.yaml --retrieval-only --no-fallback --require-source notes --json
```

Local commands must not print note bodies, LINE text, file names, paths, GPS,
OCR, captions, or personal names.

## Privacy and security

Diagnostics expose aggregate counts, source names, and safe evidence ids only.
They do not print note title/body, LINE text, image captions, filenames, full
paths, GPS, or OCR. Tests use synthetic data.

## Performance and hardware

No GPU impact. Per-source text search runs a few bounded SQLite queries per
requested source. This is acceptable for smoke and retrieval limits.

## Rollback

Remove source-table filtering and `--require-source`, restore single global text
search calls, and remove docs/tests. No source data or schema rollback is
required.

## Open questions

None blocking.
