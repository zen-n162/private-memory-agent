# ExecPlan: Phase 8-F3 Real Evidence JSON Output Control

## Goal

Make real-model E2E smoke produce valid structured JSON from DeepSeek/R1-style
outputs over real retrieved evidence, or provide precise privacy-safe
diagnostics when it cannot. The one-query real-model workflow should remain the
safe default validation path.

## Non-goals

- Do not improve final answer quality beyond smoke-grade structured validation.
- Do not run all real-model queries by default.
- Do not print raw private source content.
- Do not print raw model output by default.
- Do not start or manage model servers automatically.
- Do not require real DeepSeek, GPU, model files, network, or private data in
  unit tests.

## Current state

Phase 8-F2 added strict answer parsing, `<think>` stripping, JSON smoke,
one-shot repair retry, and safe extraction diagnostics. Local real evidence E2E
can still fail when the leader spends the small token budget on reasoning or
extra explanation instead of a JSON object. The relevant code is in:

- `src/private_memory_agent/agent/leader.py`
- `src/private_memory_agent/e2e.py`
- `src/private_memory_agent/runtime/clients.py`
- `src/private_memory_agent/cli.py`
- `tests/test_leader_agent.py`
- `tests/test_e2e_smoke.py`
- `tests/test_runtime_clients.py`

## Proposed design

- Strengthen the leader prompt for smoke JSON:
  - first output character must be `{`
  - last output character must be `}`
  - no markdown, explanation, `<think>`, or chain-of-thought
  - allowed evidence ids and allowed source labels are listed explicitly
- Keep the smoke answer schema minimal and compatible with the existing
  `Answer` model.
- Make OpenAI-compatible `response_format={"type":"json_object"}` explicit via
  `--response-format-json` for real-model E2E. Default remains plain prompt
  control so unsupported endpoints are not assumed to support JSON mode.
- Keep JSON repair retry, but do not resend full evidence by default. The retry
  prompt provides only a conservative JSON object built from already allowed
  evidence ids and source labels.
- Add safe metadata diagnostics:
  - `contains_json_like_object`
  - `contains_think_tag`
  - `contains_fenced_json`
  - `extraction_attempts`
  - `json_retry_used`
  - `json_retry_succeeded`
  - `allowed_evidence_count`
  - `allowed_sources`
- Add `--show-model-output-metadata` and `--show-model-output`. The raw-output
  option is explicit, truncated, and documented as potentially private.

## Data contracts

Extend `AnswerDiagnostics`:

- `contains_json_like_object: bool`
- `contains_think_tag: bool`
- `contains_fenced_json: bool`
- `extraction_attempts: int`
- `json_retry_used: bool`
- `json_retry_succeeded: bool`
- `allowed_evidence_count: int`
- `allowed_sources: tuple[str, ...]`
- `raw_model_output_preview: str | None`

Extend `E2ESmokeOptions`:

- `response_format_json: bool`
- `show_model_output_metadata: bool`
- `show_model_output: bool`

Extend `E2ESmokeQueryResult` with the same safe diagnostics, plus optional
truncated `raw_model_output_preview` only when explicitly requested.

## Files to change

- `.agent/execplans/phase-8f3-real-evidence-json-output-control.md`
- `src/private_memory_agent/agent/leader.py`
- `src/private_memory_agent/e2e.py`
- `src/private_memory_agent/cli.py`
- `tests/test_leader_agent.py`
- `tests/test_e2e_smoke.py`
- `docs/MODEL_RUNTIME.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add allowed evidence/source extraction helpers and prompt lines.
2. Extend `AnswerDiagnostics` with safe response metadata and retry metadata.
3. Thread `response_format_json`, model-output metadata, and raw-output preview
   flags through E2E options and CLI.
4. Make real-model E2E pass `response_format` only when explicitly requested.
5. Include retry status and safe model-output metadata in JSON/human E2E output.
6. Add synthetic tests for direct success, prefixed JSON, `<think>` JSON,
   retry success/failure, unknown evidence/source rejection, optional
   `response_format`, raw-output hiding, and safe metadata.
7. Update docs and Japanese overview.
8. Run unit tests and one-query local smoke commands.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
pma models ping leader --config configs/paths.local.yaml --json-smoke --max-tokens 128 --timeout-seconds 300
pma e2e smoke --config configs/paths.local.yaml --real-model --query-limit 1 --timeout-seconds 600 --max-tokens 512 --json
```

Do not run all real-model queries by default.

## Privacy and security

Default output never includes raw LINE text, note bodies, captions, filenames,
paths, GPS, EXIF, OCR, or raw model output. Metadata output reports only boolean
shape signals and lengths. Raw model output requires `--show-model-output`,
prints a warning, and is truncated because it may contain evidence-derived
private content.

## Performance and hardware

No new GPU requirement. Real-model local smoke should begin with
`--query-limit 1`, compact evidence, and token budgets such as `256`, `512`, or
`1024` depending on the DeepSeek/R1 server behavior on RTX 4500 Ada 24GB.

## Rollback

Remove the new CLI flags, diagnostic fields, prompt changes, and explicit
response-format option. No database schema changes or source data changes are
required.

## Open questions

None blocking.
