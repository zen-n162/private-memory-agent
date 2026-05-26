# ExecPlan: Phase 9-H5 Real-Model Response Contract Hardening

## Goal

Make every local chat UI/API path, especially `mode=real-model`, return the
same complete response contract for success, structured failures, and defensive
exceptions.

## Non-goals

- Do not change retrieval quality or DeepSeek answer quality.
- Do not run large real-model batches.
- Do not expose raw prompts, model output, LINE text, note bodies, captions,
  OCR, GPS, EXIF, filenames, full paths, or private snippets.
- Do not require real model endpoints in unit tests.

## Current state

- Phase 9-H4 added a stable chat contract, but real-model failures can still be
  normalized too late or treated as sparse failed run payloads.
- The background run registry catches unexpected exceptions and returns safe
  metadata, but it does not classify real-model answer-generation and preflight
  failures precisely enough.
- `/api/system/status` does not expose schema/version metadata, making it harder
  to confirm whether the running server is the updated code.

## Design

Centralize response normalization in the chat contract helper:

- `ensure_chat_response_contract(...)` fills all required top-level fields for
  all modes.
- `build_chat_error_payload(...)` builds complete structured error payloads for
  request validation, preflight, retrieval, answer generation, validation, and
  defensive failures.
- A new contract helper classifies real-model exceptions from trace metadata:
  DeepSeek/leader failures in `answer_synthesis` become
  `failure_stage=answer_generation`, while leader endpoint/preflight failures
  become `failure_stage=preflight`.

For valid background runs, the trace recorder already emits
`ChatRunRegistry queue_chat_run`, and `run_chat_console_query` emits
`ChatConsoleRequest receive_local_query`. If execution fails, the result endpoint
should still return `mode=real-model`, a failed `current_status`, safe privacy
defaults, and trace events.

Add API/UI schema version metadata to `/api/system/status`.

## Files to change

- `src/private_memory_agent/api/contract.py`
- `src/private_memory_agent/api/runs.py`
- `src/private_memory_agent/api/console.py`
- `src/private_memory_agent/api/schemas.py`
- `src/private_memory_agent/api/ui.py`
- `tests/test_api_console.py`
- `tests/test_api.py`
- `docs/MODEL_RUNTIME.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add chat API response schema version constants and required key list.
2. Add real-model-aware failure classification from trace events and exception
   classes.
3. Make defensive background run failures produce complete chat error payloads
   with mode preserved.
4. Normalize non-success real-model payloads so answer-generation and
   validation failures have top-level `failure_stage`, `failure_actor`,
   `error_class`, and failed `current_status`.
5. Expose API/UI response schema versions and app version in system status.
6. Add regression tests for complete schema across retrieval-only, fake-model,
   real-model success-like payloads, real-model runtime failure, validation
   failure, preflight failure, and UI strings.
7. Update docs and overview.

## Verification

- `python -m pytest -q`
- `python scripts/check_overview_html.py`
- Optional manual real-model UI check after restarting the API server and hard
  refreshing the browser.

## Privacy

All failure messages are sanitized. Trace events contain only safe summaries and
metadata. The UI should never show raw prompts, chain-of-thought, raw model
output, raw private evidence, paths, GPS, EXIF, OCR, or full snippets by
default.
