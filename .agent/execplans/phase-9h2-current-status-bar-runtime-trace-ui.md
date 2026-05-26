# ExecPlan: Phase 9-H2 Current Status Bar and Compact Runtime Trace UI

## Goal

Make the local chat UI feel like an active multi-agent console by adding a compact Current Status Bar near the Run button and a polling run API that exposes privacy-safe current status, recent completed steps, model/tool usage, and final results.

## Non-goals

- Do not expose raw chain-of-thought, raw prompts, raw model output, raw LINE text, note bodies, captions, OCR, GPS, EXIF, filenames, or full paths.
- Do not implement full WebSocket/SSE streaming in this phase.
- Do not require model servers, private data, GPU, or network access in tests.
- Do not remove the existing synchronous `/api/chat/query` endpoint.

## Current state

- Phase 9-H added `AgentTraceEvent`, `AgentTraceRecorder`, `/api/chat/query` trace events, and a detailed Agent Runtime Trace panel.
- `/api/chat/query` is synchronous, so the UI only receives trace events after completion.
- The UI status line near Run only says a generic running/done message.

## Proposed design

Add a lightweight in-memory run registry for local FastAPI app instances:

- `POST /api/chat/query/start` starts a background thread and returns `run_id` plus initial status.
- `GET /api/chat/runs/{run_id}/status` returns a `CurrentStatus` payload derived from recorded trace events.
- `GET /api/chat/runs/{run_id}/events` returns trace events and usage summaries.
- `GET /api/chat/runs/{run_id}/result` returns the final console payload when complete.

The existing synchronous endpoint remains available and shares the same trace recorder. The UI switches to start/poll/result so the Current Status Bar can update while the request is executing. If the polling flow fails, the UI can surface a safe error.

## Data contracts

`CurrentStatus`:

- `run_id`
- `status`: `idle | queued | running | succeeded | failed`
- `current_step`: actor type/name, model id, action, stage, display message, step index/total, started_at
- `recent_steps`: latest 2-3 completed safe step summaries
- `next_step_hint`
- `elapsed_ms`
- `warnings`
- `model_usage_summary`, `tool_usage_summary`, `fallback_summary`

Trace display messages are mapped from safe action/status metadata to Japanese UI text.

## Files to change

- `src/private_memory_agent/tracing.py`
- `src/private_memory_agent/api/console.py`
- Add `src/private_memory_agent/api/runs.py`
- `src/private_memory_agent/api/app.py`
- `src/private_memory_agent/api/schemas.py`
- `src/private_memory_agent/api/ui.py`
- Tests in `tests/test_api_console.py` or a new `tests/test_run_status.py`
- `docs/ROADMAP.md`, `docs/RETRIEVAL.md`, `docs/MODEL_RUNTIME.md`, `docs/overview_ja.html`

## Implementation steps

1. Add CurrentStatus helpers and Japanese action-message mapping in `tracing.py`.
2. Make `AgentTraceRecorder` safe for concurrent background writes and status polling.
3. Allow `run_chat_console_query` to accept an existing trace recorder.
4. Add in-memory run registry that executes console queries in a daemon thread.
5. Add FastAPI start/status/events/result endpoints.
6. Update UI to use polling, display a Current Status Bar, model/tool chips, and grouped/collapsed runtime timeline.
7. Add synthetic tests for status payloads, failure display, grouped timeline markup, usage summaries, and privacy-safe output.
8. Update docs and overview.

## Tests and verification

- `python -m pytest -q`
- `python scripts/check_overview_html.py`
- Optional manual UI smoke with `pma api serve --config configs/paths.local.yaml --host 127.0.0.1 --port 8787`.

## Privacy and security

All status and trace payloads use safe summaries and count-only metadata. Raw prompts, chain-of-thought, raw model output, raw evidence, snippets, filenames, paths, GPS, EXIF, and OCR remain hidden by default.

The run registry is process-local and intended for localhost only. It stores only final console payloads that already obey console privacy controls.

## Performance and hardware

Polling is lightweight and local. No additional model calls are introduced. Background execution uses Python threads and does not require GPU.

## Rollback

Remove run registry endpoints and restore the UI to the synchronous `/api/chat/query` call. Existing synchronous query behavior remains unchanged during this phase.

## Open questions

None blocking. SSE/WebSocket can be added later on top of the same status contract.
