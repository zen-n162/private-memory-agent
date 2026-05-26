# ExecPlan: Phase 9-H4 UI/API Contract and Failed-Before-Agent Diagnostics

## Goal

Make chat UI/API failures distinguishable from actual DeepSeek/tool/agent failures by returning a stable structured chat response contract for normal results and error results, including request-validation and pre-agent failures.

## Non-goals

- Do not change retrieval quality, answer quality, or temporal event search behavior.
- Do not expose raw private data, prompts, chain-of-thought, raw model output, LINE text, note bodies, captions, OCR, GPS, EXIF, filenames, or full paths.
- Do not remove the existing synchronous `/api/chat/query` or polling run API.
- Do not require live model servers or private data in tests.

## Current state

- `/api/chat/query` returns complete console payloads when execution succeeds, but FastAPI request validation and some route exceptions can return default error shapes such as `{"detail": ...}`.
- The UI can try to render those default error shapes as if they were normal chat payloads, producing `mode=undefined`, `n/a` trace fields, and `Agent unknown` failed states.
- The polling registry can report a failed run with sparse metadata if execution fails before agent trace events are created.

## Proposed design

Add a stable chat error payload builder and failure-stage vocabulary. All chat endpoints should return or embed the same core fields: `ok`, `run_id`, `mode`, `answer`, top-level answer status fields, `failure_stage`, `failure_actor`, `current_status`, `trace_events`, `trace_summary`, `privacy`, `warnings`, `candidate_dates`, `evidence`, `model_usage_summary`, and `tool_usage_summary`.

Add FastAPI request validation handling for `/api/chat/*` paths so invalid mode or missing required fields produce `failure_stage=request_validation` with a complete structured response. Route-level exceptions use the same builder with a stage such as `config_loading`, `preflight`, or `unknown`.

Update the UI to validate required response fields before rendering. If a response is malformed, show an explicit `Invalid API response: missing field X` message rather than rendering `n/a` everywhere. If `failure_stage=request_validation`, show a request-format failure message instead of `Agent unknown`.

## Data contracts

Failure stages:

- `request_validation`
- `config_loading`
- `preflight`
- `query_understanding`
- `retrieval_planning`
- `temporal_parsing`
- `retrieval`
- `evidence_judging`
- `answer_generation`
- `answer_validation`
- `privacy_filtering`
- `ui_response_rendering`
- `unknown`

Stable chat payload fields are required on both success and structured error responses.

## Files to change

- Add or update `src/private_memory_agent/api/contract.py`
- `src/private_memory_agent/api/app.py`
- `src/private_memory_agent/api/runs.py`
- `src/private_memory_agent/api/schemas.py`
- `src/private_memory_agent/api/ui.py`
- `tests/test_api_console.py` and/or `tests/test_api.py`
- `docs/MODEL_RUNTIME.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add a stable chat error payload builder with privacy defaults and failure-stage labels.
2. Add request-validation exception handling for chat API paths.
3. Ensure synchronous chat route exceptions return structured error payloads.
4. Ensure polling result/status endpoints expose structured failure metadata even before agent trace starts.
5. Add UI response contract validation and failed-before-agent copy.
6. Add tests for valid schema, invalid mode, missing question, backend exception payloads, mode preservation, privacy defaults, and trace initialization.
7. Update docs and overview.

## Tests and verification

- `python -m pytest -q`
- `python scripts/check_overview_html.py`
- Optional manual UI smoke with valid and invalid local requests.

## Privacy and security

Error payloads expose only safe messages, failure stages, statuses, and count-only metadata. They must not include raw request bodies, private snippets, paths, prompts, model output, chain-of-thought, GPS, EXIF, OCR, or captions.

## Performance and hardware

No new model calls or GPU work. Error payload generation is constant-time metadata assembly.

## Rollback

Remove the contract/error handler and UI contract validation changes. Existing success-path chat behavior remains intact.

## Open questions

None blocking.
