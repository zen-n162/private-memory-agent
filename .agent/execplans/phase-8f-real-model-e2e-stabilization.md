# ExecPlan: Phase 8-F Real-Model E2E Stabilization

## Goal

Make `pma e2e smoke --real-model` controllable and diagnosable for the local
DeepSeek leader server. The first successful target is one short real-model
query with strict structured JSON, preserved evidence ids, privacy-safe output,
and clear timeout diagnostics.

## Non-goals

- Do not improve answer quality beyond structured smoke validation.
- Do not start or manage model servers automatically.
- Do not call external APIs or non-local endpoints by default.
- Do not print raw LINE text, note bodies, photo captions, filenames, paths,
  GPS, EXIF, OCR, or private names.
- Do not require DeepSeek, GPU, network, or real private data in unit tests.

## Current state

Retrieval-only and fake-model E2E smoke pass with photo, LINE, and note
evidence. The configured DeepSeek leader server is expected at
`http://127.0.0.1:8080/v1` and `/v1/models` reports
`DeepSeek-R1-0528-Qwen3-8B-UD-Q4_K_XL.gguf`.

The E2E path already supports real-model mode and leader endpoint preflight in
the current workspace, but it needs stronger runtime controls:

- one-query execution via `--query-limit` and `--query-id`
- generation timeout override
- small `max_tokens`
- compact evidence packet
- direct chat smoke command
- timeout diagnostics with prompt size and model metadata

## Proposed design

- Extend E2E options and CLI with:
  - `--max-tokens`
  - `--max-evidence-items`
  - `--max-evidence-chars`
  - `--compact-evidence` / `--no-compact-evidence`
- Keep `--query-limit` and improve `--query-id` so labels like `query_1` also
  work.
- Default real-model smoke to `max_tokens=256`, `temperature=0.2`, compact
  evidence, at most three evidence items, and 2000 evidence characters.
- Build a compact redacted evidence packet for model prompts. Normal output
  remains ids/counts only.
- Request OpenAI-compatible JSON mode with
  `response_format={"type":"json_object"}` for real-model E2E leader calls,
  while still accepting plain, fenced, or surrounded JSON in the parser.
- Add direct `pma models ping <model> --chat-smoke` to test a tiny local chat
  completion without touching the database.
- Add per-query safe diagnostics: endpoint, served model, timeout, max tokens,
  approximate prompt chars, and evidence items sent.

## Data contracts

Extend `E2ESmokeOptions`:

- `max_tokens: int`
- `temperature: float`
- `max_evidence_items: int`
- `max_evidence_chars: int`
- `compact_evidence: bool`

Extend `E2ESmokeQueryResult`:

- `model_id`
- `endpoint_url`
- `timeout_seconds`
- `max_tokens`
- `prompt_chars`
- `evidence_sent_count`

Add `ChatSmokeResult` for direct endpoint smoke:

- `ok`
- `model_id`
- `served_model_name`
- `endpoint_url`
- `response_chars`
- `max_tokens`
- `timeout_seconds`
- `warnings`

No database schema changes.

## Files to change

- `.agent/execplans/phase-8f-real-model-e2e-stabilization.md`
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

1. Add E2E max-token and evidence-budget options.
2. Add compact redacted evidence packing and prompt-size diagnostics.
3. Pass `max_tokens`, temperature, timeout, and served model name into the
   leader client.
4. Enable JSON response format for real-model leader calls.
5. Improve `--query-id query_N` selection.
6. Add direct chat smoke helper and CLI option.
7. Add synthetic tests for query limiting, timeout, max tokens, compact
   evidence, JSON extraction, timeout errors, and privacy-safe output.
8. Update docs and Japanese overview.
9. Run unit tests and safe local smoke commands.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
pma models ping leader --config configs/paths.local.yaml
pma models ping leader --config configs/paths.local.yaml --chat-smoke --max-tokens 64 --timeout-seconds 300
pma e2e smoke --config configs/paths.local.yaml --real-model --query-limit 1 --timeout-seconds 300 --max-tokens 256 --json
```

Do not run all real-model smoke queries by default.

## Privacy and security

The E2E model prompt uses a compact redacted evidence packet by default and
does not include raw private source content. Output contains only counts,
source types, evidence ids, endpoint metadata, prompt sizes, and sanitized error
classes/messages. The leader prompt treats evidence as untrusted data and the
Evidence Critic validates references and weak-evidence uncertainty.

## Performance and hardware

Real-model E2E can be slow on RTX 4500 Ada 24GB. The default one-query manual
command uses `--max-tokens 256` and `--timeout-seconds 300`. If a
DeepSeek-style reasoning model exhausts that budget before final JSON, retry the
same one-query command with `--max-tokens 512` or `--max-tokens 1024`. Unit
tests use fake HTTP transports and do not require GPU.

## Rollback

Remove the added CLI flags, restore full redacted evidence packing, and remove
the chat-smoke helper. No source data or schema rollback is required.

## Open questions

None blocking.
