# ExecPlan: Phase 9-A Local Chat UI / Evidence Agent Console

## Goal

Add a lightweight localhost-only browser console for Private Memory Agent. The
console lets a local developer ask a question and inspect privacy-safe answer
status, evidence ids, source coverage, relevance metadata, retrieval repair
status, leader-plan metadata, and system status.

## Non-goals

- Do not build a polished production frontend.
- Do not expose the API outside localhost.
- Do not display raw LINE text, note bodies, photo captions, filenames, paths,
  GPS, EXIF, OCR, or raw model output by default.
- Do not add new retrieval rules or run large real-model batches.
- Do not shell out to the CLI for the normal request path.

## Current state

- `src/private_memory_agent/api/app.py` already creates a local FastAPI app and
  serves a minimal `/ui` page backed by `POST /api/query`.
- `pma api serve` defaults to `127.0.0.1` and rejects non-loopback hosts.
- `src/private_memory_agent/e2e.py` already provides a privacy-safe E2E query
  path with retrieval-only, fake-model, and real-model modes.
- Leader planning, deterministic relevance judging, semantic retrieval, and
  reranker interfaces already exist.
- API tests use synthetic DB data and are skipped inside the Codex sandbox unless
  explicitly enabled because FastAPI TestClient can hang there.

## Proposed design

Add a small API console service under `private_memory_agent.api.console`:

- `run_chat_query()` builds one `E2ESmokeQuery` from a submitted question.
- It optionally creates a retrieval plan using deterministic planning by
  default, or the configured leader for explicit real-model mode.
- It calls `run_e2e_smoke()` directly, never via CLI shell-out.
- It transforms the E2E result into a compact, privacy-safe response for the UI.
- It can run one repair attempt by expanding retrieval text from specific plan
  concepts when no usable evidence is found.

FastAPI adds:

- `GET /ui`
- `POST /api/chat/query`
- `GET /api/system/status`

The UI is a self-contained HTML document with inline CSS and plain JavaScript.
It posts to `/api/chat/query`, shows answer status and metadata, and keeps answer
text/snippets hidden unless explicitly toggled.

## Data contracts

Request:

- `question`
- `mode`: `retrieval-only`, `fake-model`, `real-model`
- `sources`
- `leader_plan`
- `leader_rerank`
- `semantic`
- `semantic_model`
- `reranker`
- `retrieval_repair`
- `strict_relevance`
- `show_answer`
- `show_snippets`
- `limit`
- `timeout_seconds`
- `max_tokens`

Response:

- `ok`
- `mode`
- `answer`
- `evidence`
- `trace`
- `privacy`
- `warnings`

System status:

- `db_exists`
- safe count/index summaries
- model endpoint configuration status
- local-only policy

## Files to change

- Add `.agent/execplans/phase-9a-local-chat-ui-evidence-agent-console.md`
- Add `src/private_memory_agent/api/console.py`
- Update `src/private_memory_agent/api/app.py`
- Update `src/private_memory_agent/api/schemas.py`
- Update tests in `tests/test_api.py`
- Add focused service tests if useful
- Update `docs/API.md`, `docs/ROADMAP.md`, `docs/MODEL_RUNTIME.md`,
  `docs/RETRIEVAL.md`, and `docs/overview_ja.html`

## Implementation steps

1. Create chat console service and response helpers.
2. Add Pydantic request/response schemas.
3. Add `/api/chat/query` and `/api/system/status`.
4. Replace the old `/ui` HTML with an evidence-first agent console.
5. Add synthetic tests for service behavior and endpoint shape.
6. Update docs and Japanese overview.
7. Run pytest and HTML validation.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
pma api serve --help
```

If practical locally:

```bash
pma api serve --config configs/paths.local.yaml --host 127.0.0.1 --port 8787
```

Then open `http://127.0.0.1:8787/ui`.

## Privacy and security

- Default mode is retrieval-only.
- `show_answer` and `show_snippets` are both false by default.
- Snippets, if requested, are truncated and redacted by existing E2E helpers.
- Raw model output is never returned by this UI path.
- No source filenames, paths, GPS, EXIF, OCR, raw LINE text, note bodies, or
  full captions are included by default.
- API serve remains localhost-only.

## Performance and hardware

- Default UI mode does not call real models.
- Real-model mode is explicit and supports timeout and max token controls.
- Semantic retrieval is optional and can use persisted local embeddings.
- No GPU is required for unit tests.

## Rollback

Remove the new console service, schemas, routes, tests, and docs additions.
Existing `/api/query`, ingestion endpoints, and CLI commands remain unchanged.

## Open questions

- Whether future UI phases should add authenticated localhost sessions.
- Whether raw snippets should require an additional server-side config gate.
