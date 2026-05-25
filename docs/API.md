# Local API

Phase 6-A exposes a local FastAPI API for private-memory-agent. Phase 6-B adds a
minimal browser UI served by the same localhost app. Phase 9-A replaces the
browser page with an evidence-first local agent console while keeping the API
localhost-only.

## Localhost-Only Assumption

The API has no authentication in this phase. It is intended only for local tools
running on the same machine.

`pma api serve` binds to `127.0.0.1` by default and rejects non-loopback hosts.
Do not proxy it to the public internet or bind it to `0.0.0.0`.

```bash
pma api serve --host 127.0.0.1 --port 8000
```

OpenAPI docs are available locally at:

```text
http://127.0.0.1:8000/api/docs
```

The minimal local UI is available at:

```text
http://127.0.0.1:8000/ui
```

## Endpoints

- `GET /api/health`
- `POST /api/query`
- `POST /api/chat/query`
- `GET /api/system/status`
- `POST /api/ingest/photos`
- `POST /api/ingest/line`
- `POST /api/ingest/notes`
- `GET /api/events`
- `GET /api/entities`

## Evidence-First Agent Console

The `/ui` page is a small FastAPI-served HTML document. It has no frontend build
step, external CDN, external font, or JavaScript dependency. It submits query
requests to `POST /api/chat/query` and reads count-only system status from
`GET /api/system/status`.

The console defaults to retrieval-only mode. Answer text is visible by default
in the local-only console so it behaves like a chat interface. Real-model
generation and evidence snippets remain explicit choices. It displays:

- answer success
- answer conclusion when `show_answer` is enabled, which is the UI default
- confidence
- unknowns
- used sources
- evidence references
- evidence ids
- source coverage
- per-evidence relevance metadata
- leader-plan counters
- semantic/reranker candidate counts
- retrieval repair status
- privacy status

Snippets are hidden by default. If `show_snippets` is enabled, the API returns
truncated/redacted snippets only. Raw model output is not returned by the
console endpoint. Answer text may still contain private evidence-derived
information, so do not paste local answer output into public chats when it is
private.

## Chat Query Endpoint

`POST /api/chat/query` is a UI-facing adapter over the existing E2E retrieval
pipeline. It does not shell out to the CLI.

Useful request fields:

- `mode`: `retrieval-only`, `fake-model`, or `real-model`
- `sources`: `photos`, `line`, `notes`
- `leader_plan`
- `leader_rerank`
- `semantic`
- `semantic_model`
- `reranker`
- `retrieval_repair`
- `strict_relevance`
- `show_answer`
- `show_snippets`
- `timeout_seconds`
- `max_tokens`

Default UI output contains the answer text plus counts, ids, source labels,
relevance metadata, and status. It does not include raw LINE text, note bodies,
captions, file names, paths, GPS, EXIF, OCR, raw model output, full retrieval
plans, or full evidence snippets.

## System Status Endpoint

`GET /api/system/status` returns privacy-safe operational metadata:

- DB existence
- source and index counts when the DB exists
- embedding model count breakdown
- configured model endpoints
- localhost/privacy flags

Endpoint checks are count/configuration only by default. The status endpoint does
not enumerate source directories or print private file paths.

## Privacy Defaults

General API responses are redacted by default. The legacy `/api/query` path
hides query text, answer text, evidence snippets, event titles, entity names,
aliases, and candidate values unless a request asks to show private data and
config also enables `log_private_data`.

The UI-facing `/api/chat/query` path shows answer text by default for local chat
usability, but keeps raw evidence snippets, filenames, paths, GPS, EXIF, OCR,
raw LINE messages, note bodies, captions, full retrieval plans, and raw model
output hidden unless an explicit local debugging option allows a safe/truncated
view.

Ingest endpoints return count-only summaries. They do not print filenames, raw
LINE messages, note bodies, OCR text, GPS coordinates, or private paths.

The UI does not read SQLite directly or enumerate local files. It only renders
the API response for the current request.

The API tests use FastAPI `TestClient`, temporary SQLite databases, and
synthetic fixtures only. They do not require model servers, GPU, network, Docker,
or private source data.
