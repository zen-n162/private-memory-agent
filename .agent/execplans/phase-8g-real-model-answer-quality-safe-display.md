# ExecPlan: Phase 8-G Real-model Answer Quality and Safe Display

## Goal

Make real-model E2E smoke results inspectable without changing the default privacy-safe output. Add explicit answer display, optional snippet display, and answer audit metrics so the user can evaluate DeepSeek leader responses after retrieval and structured JSON validation are working.

## Non-goals

- Do not improve final answer quality with new retrieval/model algorithms.
- Do not run large real-model batches by default.
- Do not expose raw LINE text, note bodies, captions, filenames, paths, GPS, EXIF, OCR, or raw model output by default.
- Do not ingest, annotate, or modify real source files.
- Do not enable remote endpoints by default.

## Current state

The E2E workflow in `src/private_memory_agent/e2e.py` supports dry-run, retrieval-only, fake-model, and real-model modes. Phase 8-F3 added query limiting, real-model timeouts, compact evidence, JSON retry, strict answer parsing diagnostics, and optional raw model-output preview. The current `E2ESmokeQueryResult` stores answer confidence, evidence references, and used sources, but not gated display text or answer audit metrics. The CLI in `src/private_memory_agent/cli.py` has `pma e2e smoke` flags for real-model control and JSON diagnostics, but no `--show-answer` or `--show-snippets`.

## Proposed design

Add display controls to `E2ESmokeOptions`:

- `show_answer`: include/display the structured answer fields that may contain evidence-derived text.
- `show_snippets`: include/display truncated redacted evidence snippets only when explicitly requested.

Default output remains counts, source labels, evidence IDs, and diagnostics only. Internally, successful answers will record non-private counts for audit even when answer text is hidden.

Add an answer audit summary to the E2E report. The audit will count answer successes, validation errors, retry usage/success, confidence average, evidence-reference coverage, unknown evidence reference failures, answer source counts, and queries with empty `used_sources` or `unknowns`.

## Data contracts

Extend `E2ESmokeQueryResult` with:

- `answer_conclusion: str | None`
- `answer_unknowns: tuple[str, ...]`
- `answer_unknown_count: int | None`
- `answer_evidence_reference_count: int | None`
- `answer_used_source_count: int | None`
- `safe_snippets: tuple[dict[str, str], ...]`

Add `E2EAnswerAudit` with:

- `answer_succeeded_count`
- `answer_validation_error_count`
- `retry_used_count`
- `retry_success_count`
- `average_confidence`
- `evidence_reference_coverage`
- `unknown_evidence_reference_count`
- `answer_source_counts`
- `queries_with_empty_used_sources`
- `queries_with_empty_unknowns`

## Files to change

- `src/private_memory_agent/e2e.py`
- `src/private_memory_agent/cli.py`
- `tests/test_e2e_smoke.py`
- `docs/MODEL_RUNTIME.md`
- `docs/RETRIEVAL.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add display and audit fields to E2E dataclasses and JSON serialization.
2. Populate answer display fields only when `show_answer` is enabled.
3. Populate snippet display fields only when `show_snippets` is enabled, using redacted/truncated evidence snippets.
4. Compute answer audit metrics from per-query results.
5. Update human report formatting to display answer fields and snippets only under explicit flags.
6. Add CLI flags `--show-answer` and `--show-snippets`.
7. Add tests for default hiding, explicit answer display, explicit snippet display, audit metrics, unknown evidence reference audit, query id/limit behavior, and privacy-safe output.
8. Update docs and Japanese overview.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

If the local leader server is running, run:

```bash
pma e2e smoke --config configs/paths.local.yaml --real-model --query-limit 1 --timeout-seconds 600 --max-tokens 512 --json
pma e2e smoke --config configs/paths.local.yaml --real-model --query-limit 1 --timeout-seconds 600 --max-tokens 512 --show-answer
```

Do not run full real-model batches by default.

## Privacy and security

Default E2E output remains privacy-safe and hides answer text, snippets, raw evidence, raw model output, filenames, full paths, GPS, EXIF, OCR, LINE text, note bodies, and captions. `--show-answer` is explicit because answer text may be evidence-derived. `--show-snippets` is separate, explicit, truncated, and uses already-redacted evidence. The report warns when user-visible answer or snippet display is requested.

## Performance and hardware

No new GPU or model loading behavior is introduced. Real-model runs should still start with `--query-limit 1`, `--timeout-seconds 600`, and a small `--max-tokens` value. Default tests use synthetic data and fake or monkeypatched clients.

## Rollback

Remove the new E2E display/audit fields, CLI flags, and tests. Existing real-model E2E behavior would remain unchanged because answer generation and JSON parsing paths are not being replaced.

## Open questions

None blocking.
