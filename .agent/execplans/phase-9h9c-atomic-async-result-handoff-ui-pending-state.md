# Phase 9-H9c: Atomic Async Result Handoff and UI Pending State

## Goal

Make the async chat UI path behave like synchronous `/api/chat/query` once a
run completes. A status of `succeeded` must mean the final normalized result is
stored and immediately retrievable from `/api/chat/runs/{run_id}/result`.

## Problem

Manual sync API checks are correct, but the UI can still show
`ChatRunNotReady` as a final failure. This indicates the frontend can observe a
terminal status before the result handoff is ready, or render a pending result
payload as final output.

## Plan

1. Add explicit run lifecycle metadata:
   - `result_ready`
   - `result_available`
   - `result_saved_at`
   - `terminal`
   - optional `finalizing` status
2. Make `ChatRunRegistry._execute` finalize atomically:
   - build normalized chat response
   - store result
   - set `result_ready=true`
   - set `result_saved_at`
   - then set `status=succeeded`
3. Enforce `/status` invariants:
   - `status=succeeded` implies `result_ready=true`
   - if the internal state is inconsistent, expose `status=finalizing`
4. Improve `/result` pending behavior:
   - before ready, return structured `ChatRunNotReady` with current run status
     and `result_ready=false`
   - avoid treating not-ready as agent failure
5. Update UI polling:
   - keep polling while status is queued/running/finalizing
   - fetch `/result` only when `status=succeeded && result_ready=true`, or
     failed with a stored failure payload
   - retry `ChatRunNotReady` as pending
6. Keep source validation and final-result-only rendering from H9b.
7. Bump API/UI schema versions to `2026-05-26.9h9c`.
8. Add synthetic backend and HTML/JS contract tests.
9. Update docs and overview.
10. Run:
    - `python -m pytest -q`
    - `python scripts/check_overview_html.py`

## Privacy

Lifecycle metadata must remain safe: statuses, counts, run ids, schema
versions, and safe error classes only. Do not log or return raw evidence,
private text, prompts, model outputs, filenames, paths, GPS, EXIF, OCR, LINE
text, note bodies, or captions.
