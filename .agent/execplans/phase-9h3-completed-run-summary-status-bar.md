# ExecPlan: Phase 9-H3 Completed Run Summary Status Bar

## Goal

Make the local chat UI Current Status Bar useful after a run completes by replacing noisy low-level trace/event chips with a concise Done/Failed summary, while keeping the detailed grouped Runtime Timeline available separately.

## Non-goals

- Do not remove the detailed Agent Runtime Trace panel.
- Do not expose raw prompts, chain-of-thought, raw model output, LINE text, note bodies, captions, OCR, GPS, EXIF, filenames, paths, or private snippets.
- Do not add WebSocket/SSE streaming in this phase.
- Do not change retrieval/answer quality logic.

## Current state

- Phase 9-H added structured trace events and model/tool/fallback summaries.
- Phase 9-H2 added a polling run API and a Current Status Bar.
- During and after execution the Current Status Bar currently renders many model/tool chips and recent low-level steps, including `not_used` models/tools and validator/privacy/UI renderer entries, which is too noisy after completion.

## Proposed design

Add completed-state summary helpers in the UI and backend status payload:

- While `status=queued|running`, keep current live status behavior: actor, action, step count, elapsed time, recent 2-3 steps.
- When `status=succeeded`, render a compact summary: Done, elapsed time, answer status/state, candidate date count, evidence reference count, used sources, warning count, and major used models/tools only.
- When `status=failed`, render a compact failure summary: failed actor/stage, error class, safe error message, and next action hint.
- Hide `not_used` model/tool entries by default in the completed summary, with an optional expandable section for unused tools/models.
- Keep detailed Runtime Timeline grouped and collapsed below the result panels.

## Data contracts

Extend the current status payload with optional summary metadata:

- `completion_summary`: answer state, candidate date count, evidence reference count, used sources, warning count, used model labels, used tool labels.
- `failure_summary`: failed actor, failed stage/action, error class, safe error message, suggested next action.

The UI may also derive these summaries from the final result payload when rendering after `/result` is fetched.

## Files to change

- `src/private_memory_agent/tracing.py`
- `src/private_memory_agent/api/runs.py`
- `src/private_memory_agent/api/ui.py`
- `tests/test_api_console.py`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add safe summary builders for completed and failed runs.
2. Include compact completion/failure summaries in `/api/chat/runs/{run_id}/status` when available.
3. Update `/ui` Current Status Bar rendering to branch between live mode and completed summary mode.
4. Filter `not_used` models/tools from completed summary by default and add a small optional unused details disclosure.
5. Keep detailed grouped timeline in the Agent Runtime Trace panel.
6. Add tests for running status, completed compact summary, failed summary, not-used filtering, and privacy-safe output.
7. Update docs and overview.

## Tests and verification

- `python -m pytest -q`
- `python scripts/check_overview_html.py`
- Optional manual local UI smoke with `pma api serve --config configs/paths.local.yaml --host 127.0.0.1 --port 8787`.

## Privacy and security

Completed summaries expose only counts, statuses, source labels, model/tool names, safe error messages, and safe next-step hints. They must not expose raw evidence, private snippets, prompts, chain-of-thought, paths, GPS, EXIF, OCR, or raw model output.

## Performance and hardware

The summary is derived from existing result/status payloads and trace metadata. No additional model calls, GPU work, or indexing is introduced.

## Rollback

Revert the UI rendering changes and status summary helpers. The Phase 9-H2 polling API and detailed runtime trace can remain functional.

## Open questions

None blocking.
