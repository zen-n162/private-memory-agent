# Phase 9-H8: Recovered Failure / Fallback Status Aggregation

## Goal

Fix chat UI/API status aggregation so a recoverable intermediate failure does
not mark the whole run as failed after fallback and final answer success.

## Problem

Real-model temporal queries can succeed end-to-end while one intermediate
DeepSeek Leader planning call fails and deterministic planning recovers. The
current response may still show:

- `ok=true`
- `answer_succeeded=true`
- `answer_synthesis_succeeded=true`
- but `current_status.status=failed`
- and `failure_stage=answer_generation`

This is contradictory. Final status must come from the final outcome, while
recovered intermediate failures should be reported as warnings and recovered
fallback metadata.

## Plan

1. Inspect current trace/status aggregation in `tracing.py`, `api/runs.py`,
   `api/contract.py`, and console response normalization.
2. Add recovered-failure summarization:
   - count failed events recovered by later fallback/success events
   - expose `recovered_failure_count` and safe `recovered_failures`
   - keep raw prompts/evidence hidden
3. Update status finalization rules:
   - if `ok=true` and answer synthesis succeeded, clear final failure fields
   - do not let intermediate failed trace events set run status to failed
   - preserve intermediate failures in trace, warnings, fallback summary, and
     recovered failure metadata
4. Update model usage aggregation:
   - support `partially_failed_recovered`
   - distinguish recovered failures from unrecovered failures
5. Update UI rendering:
   - show Done when top-level response succeeded
   - show recovered fallback warning in completed summary
   - hide unused/noisy trace items from status summary
6. Add synthetic regression tests:
   - leader planning failure + deterministic fallback + answer success
   - unrecovered answer generation failure remains failed
   - response contract invariants
   - UI copy avoids failed state for recovered failure
7. Update docs and `docs/overview_ja.html`.
8. Run verification:
   - `python -m pytest -q`
   - `python scripts/check_overview_html.py`

## Privacy

Recovered failure summaries must contain only actor, stage, action, error
class, fallback actor, and safe messages. They must not include raw LINE text,
note bodies, captions, OCR, filenames, GPS, EXIF, prompts, model outputs, or
private paths.
