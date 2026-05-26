# Phase 9-H8b: Final Outcome Status Aggregation and Recovered Failure Invariants

## Goal

Harden the chat API so the final run status always follows the top-level
outcome. A recovered intermediate Leader failure must not leave
`failure_stage=answer_generation` or `current_status.status=failed` when the
final answer succeeded.

## Problem

A real-model temporal run can return:

- `ok=true`
- `answer_succeeded=true`
- `answer_state=visible`

while also reporting:

- `failure_stage=answer_generation`
- `failure_actor=DeepSeek Leader`
- `current_status.status=failed`

Trace inspection shows the Leader event intent planning step failed, but
`DeterministicEventIntentPlanner` recovered via fallback and the temporal answer
was generated. This is a recovered intermediate failure, not a final failure.

## Plan

1. Re-check all direct `/api/chat/query` and polling run response paths.
2. Recompute model/tool/fallback summaries from trace events during contract
   normalization so stale embedded summaries cannot survive.
3. Ensure response schemas include recovered failure fields for query and run
   status payloads.
4. Enforce invariants:
   - `ok=true` and `answer_succeeded=true` clears final failure fields.
   - successful final outcome forces `current_status.status=succeeded`.
   - recovered failures move to `warnings`, `fallback_summary`, and
     `recovered_failures`.
   - `current_status.status=failed` is only allowed for final failures.
5. Add regression tests for:
   - recovered planning failure + fallback + answer success.
   - unrecovered planning failure/no answer.
   - answer generation failure after evidence success.
   - UI Done rendering for recovered failure.
6. Update docs and overview.
7. Run:
   - `python -m pytest -q`
   - `python scripts/check_overview_html.py`

## Privacy

Recovered failure metadata is limited to actor/stage/action/error class and
fallback actor. It must not include raw private evidence, prompts, model output,
filenames, GPS, EXIF, OCR, LINE text, note bodies, or captions.
