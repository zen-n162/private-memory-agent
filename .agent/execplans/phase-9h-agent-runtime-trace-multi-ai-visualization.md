# ExecPlan: Phase 9-H Agent Runtime Trace and Multi-AI Visualization

## Goal

Add a privacy-safe runtime trace system for the local chat console so the UI and API can show what the DeepSeek Leader, tools, retrievers, specialist models, rerankers, validators, and privacy guard did during a request.

## Non-goals

- Do not expose raw chain-of-thought, raw prompts, raw model outputs, raw LINE text, note bodies, captions, filenames, paths, GPS, EXIF, or OCR by default.
- Do not add live streaming, WebSocket, or SSE in this phase.
- Do not run model inference or large real-data batches in tests.
- Do not persist trace records to the database unless a simple optional hook already exists.

## Current state

- `/api/chat/query` is served through `src/private_memory_agent/api/app.py` and `src/private_memory_agent/api/console.py`.
- `/ui` is a self-contained static HTML/JS page in `src/private_memory_agent/api/ui.py`.
- Temporal event queries are handled by `src/private_memory_agent/temporal.py`, including EventIntentPlan, photo date search, LINE/notes support, chunking, pruning, evidence separation, and diagnostics.
- Golden/e2e retrieval flows already expose stage metadata but do not have a unified ordered runtime trace.
- Qwen3-VL is currently represented by cached photo annotations during chat temporal retrieval; live annotation is not called by the UI path.

## Proposed design

Introduce a small `private_memory_agent.tracing` module with:

- `AgentTraceEvent`: immutable event schema for privacy-safe step metadata.
- `AgentTraceRecorder`: in-memory recorder with start/end/convenience methods and JSON serialization.
- summary builders for model usage, tool usage, and fallback usage.

Thread an optional recorder through the chat console and temporal event workflow. Instrument the primary local UI path first: query receipt, date parsing, EventIntentPlan creation/fallback, photo date search, cached Qwen3-VL annotations, LINE/notes search, semantic/reranker option state, evidence judging/scoring, retrieval repair decision, answer synthesis, answer validation, privacy filtering, and UI response rendering.

The UI will render an `Agent Runtime Trace` panel after completion plus compact model/tool/fallback summaries. Details are expandable and use only safe summaries.

## Data contracts

`AgentTraceEvent` fields:

- `run_id`, `step_id`, `parent_step_id`, `timestamp`
- `actor_type`, `actor_name`, `model_id`, `provider`
- `stage`, `action`, `status`
- `safe_input_summary`, `safe_output_summary`
- `reasoning_summary`, `decision_summary`
- `error_class`, `safe_error_message`
- `duration_ms`, `token_input_count`, `token_output_count`
- `privacy_level`, `invocation_type`, `artifact_type`, `artifact_model_id`
- `metadata`

API response additions:

- `trace_events`
- `model_usage_summary`
- `tool_usage_summary`
- `fallback_summary`

## Files to change

- Add `src/private_memory_agent/tracing.py`
- Modify `src/private_memory_agent/temporal.py`
- Modify `src/private_memory_agent/api/console.py`
- Modify `src/private_memory_agent/api/ui.py`
- Modify `src/private_memory_agent/api/schemas.py` if response schema needs explicit fields
- Add or extend tests in `tests/`
- Update `docs/ROADMAP.md`, `docs/RETRIEVAL.md`, `docs/MODEL_RUNTIME.md`, and `docs/overview_ja.html`

## Implementation steps

1. Add trace schemas and summary helpers.
2. Add recorder lifecycle in the chat console and attach serialized trace fields to responses.
3. Instrument temporal event workflow with privacy-safe trace events.
4. Add cached artifact events for Qwen3-VL photo annotations and Qwen3 Swallow text extraction status where applicable.
5. Add semantic/reranker/answer/validator/privacy events in console-level orchestration.
6. Render Agent Runtime Trace and summary panels in `/ui`.
7. Add synthetic tests for trace emission, summaries, failure events, and privacy-safe output.
8. Update docs and overview.

## Tests and verification

- `python -m pytest -q`
- `python scripts/check_overview_html.py`
- Optional local UI smoke: start `pma api serve --config configs/paths.local.yaml --host 127.0.0.1 --port 8787`, ask a temporal query, and inspect the trace panel.

## Privacy and security

Trace events store only structured metadata, counts, statuses, and safe summaries. They must not store raw private evidence, prompts, chain-of-thought, raw model output, file paths, GPS, EXIF, OCR, or note/LINE bodies. Raw trace display remains out of scope and disabled by default.

## Performance and hardware

Tracing is in-memory and lightweight. It adds no GPU/VRAM requirements and does not trigger additional model calls. Unit tests use synthetic data only.

## Rollback

Remove the tracing module, revert API/UI response additions, and remove temporal/console instrumentation. Existing chat and temporal query logic should continue to work without trace fields.

## Open questions

None blocking. Live streaming can be layered onto the recorder in a later phase.
