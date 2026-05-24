# ExecPlan: Phase 8-F Real-Model E2E DeepSeek Leader

## Goal

Enable `pma e2e smoke --real-model` to safely exercise the existing local
retrieval and evidence-packing pipeline with the configured local leader model
endpoint. The command should support small, diagnosable runs, validate strict
answer JSON, preserve evidence ids, and keep normal output privacy-safe.

## Non-goals

- Do not improve final answer quality or prompt strategy beyond safe structured
  output.
- Do not start DeepSeek, llama.cpp, vLLM, Ollama, Docker, or any heavy model
  server automatically.
- Do not call external APIs.
- Do not print raw LINE text, note bodies, photo captions, filenames, paths,
  GPS, EXIF, OCR, or private names.
- Do not require real model servers, GPU, network, or private data in tests.

## Current state

Phase 8-E made no-fallback retrieval return photo, LINE, and note evidence and
fixed fake-model validation for weak LINE/note evidence. `E2ESmokeOptions`
already has `real_model`, but the implementation lacks query limiting,
real-model endpoint preflight, CLI timeout override, and robust answer JSON
extraction for reasoning-style local models.

The runtime package already has an OpenAI-compatible stdlib HTTP client,
`/models` parsing for vision preflight, and model endpoint metadata from
`configs/models.example.yaml`.

## Proposed design

- Add query selection controls to E2E smoke:
  - `--query-limit N` limits how many configured smoke queries are run.
  - `--query-id ID` selects a specific query from the safe query profile.
- Add `--timeout-seconds` for real-model answer generation.
- Add leader endpoint preflight before query execution in `--real-model` mode:
  - validate model key and endpoint config
  - call `/v1/models`
  - resolve the served model name from `served_model_name`, configured model id,
    or the first returned model
  - fail early with a sanitized report-level warning when unreachable
- Add a generic chat endpoint preflight helper in the runtime package.
- Improve leader answer JSON parsing so it can accept plain JSON, fenced JSON,
  and text surrounding the first valid JSON object.
- Keep E2E output status-only by default: answer confidence, evidence ids,
  used sources, error class, and sanitized error messages.

## Data contracts

Extend `E2ESmokeOptions` with:

- `query_limit: int | None`
- `query_id: str | None`
- `timeout_seconds: float | None`

Extend runtime with `ChatEndpointPreflightResult`:

- `model_id`
- `served_model_name`
- `endpoint_url`
- `model_ids`
- `warnings`

No database schema changes.

## Files to change

- `.agent/execplans/phase-8f-real-model-e2e-deepseek-leader.md`
- `src/private_memory_agent/runtime/clients.py`
- `src/private_memory_agent/runtime/__init__.py`
- `src/private_memory_agent/agent/leader.py`
- `src/private_memory_agent/e2e.py`
- `src/private_memory_agent/cli.py`
- `tests/test_runtime_clients.py`
- `tests/test_leader_agent.py`
- `tests/test_e2e_smoke.py`
- `docs/MODEL_RUNTIME.md`
- `docs/RETRIEVAL.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add chat endpoint preflight to runtime using existing OpenAI-compatible
   `/models` normalization.
2. Add query selection and timeout fields to E2E options and CLI.
3. Preflight the configured leader endpoint once before real-model query runs.
4. Reuse the resolved served model name and timeout when building the leader
   chat client.
5. Improve leader JSON extraction and strict validation diagnostics.
6. Add synthetic tests for preflight, query limiting, timeout, robust JSON, and
   privacy-safe errors.
7. Update docs and Japanese overview.
8. Run unit tests, overview HTML check, and a small local real-model smoke if
   the leader endpoint is reachable.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
pma models ping leader --config configs/paths.local.yaml
pma e2e smoke --config configs/paths.local.yaml --real-model --query-limit 1 --json
```

The final two commands are live local checks. If the leader server is not
running, report the clean failure and do not treat it as a unit-test failure.

## Privacy and security

Real-model E2E uses redacted packed evidence in the prompt and does not echo
query text or evidence snippets in output. Errors are sanitized through the same
safe-message path used by existing E2E smoke. Endpoint URLs are localhost/local
only unless `--allow-remote` is explicitly set.

The prompt states that retrieved evidence is untrusted data and must not be
obeyed as instructions. `EvidenceCritic` validates evidence references,
source references, uncertainty for weak evidence, and source-injection shape.

## Performance and hardware

No server is started by PMA. Real-model E2E can be slow on RTX 4500 Ada 24GB,
so the recommended first run is `--query-limit 1 --timeout-seconds 300`.
Tests use fake transports only and do not require a GPU.

## Rollback

Remove the new CLI options and E2E fields, restore direct `--real-model` client
construction without preflight, and revert JSON extraction to strict whole-text
parsing. No data or schema rollback is needed.

## Open questions

None blocking.
