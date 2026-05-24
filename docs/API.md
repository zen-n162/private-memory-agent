# Local API

Phase 6-A exposes a local FastAPI API for private-memory-agent. Phase 6-B adds a
minimal browser UI served by the same localhost app.

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
- `POST /api/ingest/photos`
- `POST /api/ingest/line`
- `POST /api/ingest/notes`
- `GET /api/events`
- `GET /api/entities`

## Minimal UI

The `/ui` page is a small FastAPI-served HTML document. It has no frontend build
step and no separate JavaScript dependencies. It submits query requests to
`POST /api/query` with selected source filters for photos, LINE, and notes.
It defaults to the fake local client for safe setup and can target a configured
OpenAI-compatible local endpoint by changing the client and model key controls.

The UI displays:

- answer conclusion
- confidence
- unknowns
- used sources
- evidence ids, source kinds, scores, and snippets

Snippets are whatever the API response returns. By default, that means redacted
safe snippets only. The private display toggle still requires the backend config
to allow private display before any unredacted text can be returned.

## Privacy Defaults

API responses are redacted by default. Query text, answer text, evidence
snippets, event titles, entity names, aliases, and candidate values are hidden
unless a request asks to show private data and config also enables
`log_private_data`.

Ingest endpoints return count-only summaries. They do not print filenames, raw
LINE messages, note bodies, OCR text, GPS coordinates, or private paths.

The UI does not read SQLite directly or enumerate local files. It only renders
the API response for the current request.

The API tests use FastAPI `TestClient`, temporary SQLite databases, and
synthetic fixtures only. They do not require model servers, GPU, network, Docker,
or private source data.
