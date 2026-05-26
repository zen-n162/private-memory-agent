# ExecPlan: Phase 9-H6 Shared Evidence Builder and Real-Model Failure Preservation

## Goal

Separate evidence building from answer synthesis in the chat API contract so
retrieval-only, fake-model, and real-model modes share the same evidence payload
shape, and real-model failures do not erase candidate dates, evidence, temporal
diagnostics, or trace events.

## Non-goals

- Do not change retrieval ranking quality or temporal event scoring.
- Do not require real DeepSeek in tests.
- Do not expose raw prompts, raw model output, LINE text, note bodies,
  captions, OCR, GPS, EXIF, filenames, full paths, or full snippets by default.
- Do not run broad real-model batches.

## Current State

- Temporal queries can return candidate dates in retrieval-only mode.
- Real-model answer failures are structured after Phase 9-H5, but explicit
  evidence-builder status fields are missing.
- UI answer failure copy can imply that nothing useful happened, even when
  retrieval/candidate-date extraction succeeded.
- Evidence counts can be derived from nested payloads, but the API does not
  expose a stable `evidence_builder_succeeded` versus
  `answer_synthesis_succeeded` split.

## Proposed Design

Keep using the existing shared retrieval/temporal services, but normalize every
chat payload into two stages:

- Evidence builder: query understanding, parsed temporal range, candidate dates,
  evidence metadata, diagnostics, trace events, source coverage, and warnings.
- Answer synthesizer: retrieval-only no-op, fake model answer, or real-model
  DeepSeek answer.

Add explicit top-level fields:

- `evidence_builder_succeeded`
- `answer_synthesis_succeeded`
- `candidate_date_count`
- `evidence_reference_count`
- `evidence_count`
- `answer_error_class`
- `answer_error_message`

For real-model answer failures, preserve `candidate_dates`, `evidence`,
`temporal_event`, `trace_events`, and diagnostics, while setting
`failure_stage=answer_generation` or `answer_validation`.

Update UI copy so evidence-builder success plus answer failure shows:
`候補日は取得できましたが、DeepSeekによる最終回答生成で失敗しました。`

## Files to change

- `src/private_memory_agent/api/contract.py`
- `src/private_memory_agent/api/schemas.py`
- `src/private_memory_agent/api/ui.py`
- `src/private_memory_agent/api/runs.py`
- `tests/test_api_console.py`
- `tests/test_temporal_events.py`
- `docs/ROADMAP.md`
- `docs/RETRIEVAL.md`
- `docs/overview_ja.html`

## Implementation Steps

1. Extend the chat response contract with evidence-builder and answer-synthesis
   status fields.
2. Ensure contract normalization derives these fields from temporal/evidence
   payloads without discarding nested results.
3. Update completed run summaries to count candidate dates from normalized
   top-level fields when present.
4. Update UI answer failure copy and required-field validation.
5. Add synthetic regression tests for real-model answer failure preserving
   evidence and temporal candidate dates.
6. Update docs and overview.

## Verification

- `python -m pytest -q`
- `python scripts/check_overview_html.py`
- Optional manual local UI check: run the same temporal question in
  retrieval-only and real-model, and confirm candidate dates remain visible if
  final answer generation fails.

## Privacy

Only safe metadata, counts, IDs, and truncated/explicitly gated snippets are
returned. No private raw evidence, prompts, model outputs, paths, GPS, EXIF, OCR,
or captions are exposed by default.
