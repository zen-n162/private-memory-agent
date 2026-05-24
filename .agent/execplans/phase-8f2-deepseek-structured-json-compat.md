# ExecPlan: Phase 8-F2 DeepSeek Structured JSON Compatibility

## Goal

Make `pma e2e smoke --real-model --query-limit 1` more compatible with
DeepSeek/R1-style local leader outputs by improving strict JSON prompting,
parsing, optional repair retry, and privacy-safe diagnostics. The desired
outcome is a validated `Answer` object with preserved evidence ids when the
local leader can produce structured JSON.

## Non-goals

- Do not improve final answer quality beyond structured smoke validation.
- Do not run all real-model smoke queries by default.
- Do not start or manage model servers automatically.
- Do not print raw model output by default.
- Do not print raw LINE text, note bodies, photo captions, filenames, full
  paths, GPS, EXIF, OCR, or private names.
- Do not require DeepSeek, GPU, network, model files, or private data in unit
  tests.

## Current state

Phase 8-F added real-model E2E controls, leader preflight, chat smoke,
`--query-limit`, timeout and token controls, compact evidence packing, and
basic JSON extraction. Local validation shows retrieval succeeds and output is
privacy-safe, but `--max-tokens 256` can fail with
`AnswerValidationError: leader answer did not contain a valid JSON object`.

The relevant modules are:

- `src/private_memory_agent/agent/leader.py`
- `src/private_memory_agent/e2e.py`
- `src/private_memory_agent/runtime/clients.py`
- `src/private_memory_agent/cli.py`
- `tests/test_leader_agent.py`
- `tests/test_e2e_smoke.py`
- `tests/test_runtime_clients.py`

## Proposed design

- Make the leader prompt show the exact JSON schema and explicitly forbid
  markdown, extra explanation, and chain-of-thought.
- Add parser diagnostics that identify the extraction strategy:
  `direct_json`, `fenced_json`, `extracted_object`, `retry_success`, or
  `failed`.
- Strip `<think>...</think>` blocks before JSON extraction.
- Keep existing strict `Answer` schema validation and Evidence Critic checks.
- Add an optional real-model repair retry. The retry asks for strict JSON only
  and reuses the same compact redacted evidence prompt; no raw model output is
  logged.
- Add safe E2E report fields for raw response length, extraction success,
  extraction strategy, and sanitized validation error class/message.
- Add `pma models ping <model> --json-smoke` using a synthetic prompt only.
- Warn when `media_annotations` outnumber photo annotation rows in the text
  index, recommending `pma index text`.

## Data contracts

Add a parsed-answer envelope:

- `answer: Answer | None`
- `raw_response_chars: int`
- `json_extraction_succeeded: bool`
- `json_extraction_strategy: str`
- `answer_validation_error_class: str | None`
- `answer_validation_error_message: str | None`

Extend `E2ESmokeOptions`:

- `json_retry: int`
- `show_model_output: bool`

Extend `E2ESmokeQueryResult` with privacy-safe diagnostics:

- `raw_response_chars`
- `json_extraction_succeeded`
- `json_extraction_strategy`
- `answer_validation_error_class`
- `answer_validation_error_message`

Add `JSONSmokeResult` for synthetic endpoint validation:

- `ok`
- `model_id`
- `served_model_name`
- `endpoint_url`
- `response_chars`
- `json_extraction_succeeded`
- `json_extraction_strategy`
- `max_tokens`
- `timeout_seconds`
- `warnings`

## Files to change

- `.agent/execplans/phase-8f2-deepseek-structured-json-compat.md`
- `src/private_memory_agent/agent/leader.py`
- `src/private_memory_agent/e2e.py`
- `src/private_memory_agent/runtime/clients.py`
- `src/private_memory_agent/runtime/__init__.py`
- `src/private_memory_agent/cli.py`
- `tests/test_leader_agent.py`
- `tests/test_e2e_smoke.py`
- `tests/test_runtime_clients.py`
- `docs/MODEL_RUNTIME.md`
- `docs/RETRIEVAL.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add extraction diagnostics and `<think>` stripping to leader JSON parsing.
2. Update the leader prompt with the explicit JSON schema and no-chain-of-thought
   instruction.
3. Add optional repair retry to `LeaderAgent.answer_with_diagnostics`.
4. Thread E2E `--json-retry` and `--show-model-output` options through CLI and
   report output.
5. Add `--json-smoke` to `pma models ping` with a synthetic prompt and parser
   validation.
6. Add index-lag warning for photo annotations not yet reflected in the text
   index.
7. Add synthetic tests for parser variants, retry success/failure, diagnostics,
   evidence-id validation, JSON smoke, and no private leakage.
8. Update docs and Japanese overview.
9. Run unit tests and safe local smoke commands.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
pma models ping leader --config configs/paths.local.yaml --chat-smoke --max-tokens 64 --timeout-seconds 300
pma models ping leader --config configs/paths.local.yaml --json-smoke --max-tokens 128 --timeout-seconds 300
pma e2e smoke --config configs/paths.local.yaml --real-model --query-limit 1 --timeout-seconds 300 --max-tokens 256 --json
```

Do not run all real-model E2E queries by default.

## Privacy and security

The model prompt uses compact redacted evidence by default. Reports include
counts, source types, safe evidence ids, endpoint metadata, response length, and
sanitized error classes/messages only. Raw model output remains hidden unless
the operator explicitly passes `--show-model-output`, which is documented as
potentially private. Evidence remains untrusted data in the prompt, and the
Evidence Critic rejects unknown evidence ids or unsupported sources.

## Performance and hardware

No new GPU requirement is introduced. Real-model local smoke can be slow on the
RTX 4500 Ada 24GB machine, so commands should start with `--query-limit 1`,
compact evidence, and conservative token budgets. Unit tests use fake clients
and fake HTTP transports.

## Rollback

Remove the new CLI flags, JSON smoke helper, parsed-answer diagnostics, and
retry path. No database schema or private source data rollback is required.

## Open questions

None blocking.
