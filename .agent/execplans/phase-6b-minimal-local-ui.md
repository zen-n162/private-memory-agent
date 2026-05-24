# ExecPlan: Phase 6-B Minimal Local UI

## Goal

Add a minimal localhost-only, evidence-first UI for local querying. The UI should
let the user enter a question, choose source filters, submit to the existing
query API, and inspect the structured answer fields and safe evidence snippets.

## Non-goals

- Do not add a large frontend framework or build pipeline.
- Do not expose the service beyond localhost.
- Do not add authentication in this phase.
- Do not display raw sensitive text unless the existing privacy mode explicitly
  allows it.
- Do not add model loading, autonomous planning, ingestion UI flows, or visual
  regression tests.

## Current state

Phase 6-A added a FastAPI app factory in `private_memory_agent.api`, strict
request/response schemas, local ingest/query/events/entities endpoints, and
`pma api serve`. The API already redacts query, answer, evidence, event, and
entity fields by default. API tests use FastAPI TestClient when the environment
allows it, with a fallback skip for the Codex network sandbox.

There is currently no browser UI.

## Proposed design

Serve a single static HTML document from the FastAPI app at `/ui`, with `/`
redirecting to `/ui`. The page will use small inline CSS and JavaScript to avoid
new packaging or frontend dependencies. It will call `POST /api/query` and pass
the selected source filters.

The page will render:

- Local-only warning.
- Query input.
- Source filter checkboxes for photos, LINE, and notes.
- Client and model key controls, defaulting to the fake local client for
  lightweight setup.
- Optional "show private if config allows" checkbox, off by default.
- Answer conclusion, confidence, unknowns, and used sources.
- Evidence list using only snippets returned by the API.

The UI text will avoid dumping private file paths, raw messages, note bodies,
OCR, GPS, or names. Because the backend response remains redacted by default,
the browser surface inherits the existing privacy guardrails.

## Data contracts

No database schema changes.

Route additions:

- `GET /`
  - redirects to `/ui`
- `GET /ui`
  - returns `text/html`

The UI submits the existing `QueryRequest` JSON shape:

```json
{
  "question": "...",
  "sources": ["line", "notes"],
  "limit": 8,
  "semantic_model": "none",
  "client": "fake",
  "show_private": false
}
```

The UI consumes the existing `QueryResponse` shape and displays `answer` and
`evidence` fields.

## Files to change

- `.agent/execplans/phase-6b-minimal-local-ui.md`
- `src/private_memory_agent/api/app.py`
- `tests/test_api.py`
- `docs/API.md`
- `docs/ARCHITECTURE.md`
- `docs/SECURITY_PRIVACY.md`

## Implementation steps

1. Add a minimal HTML renderer in the API module.
2. Add `GET /ui` and root redirect routes.
3. Add client-side source filters and structured answer rendering.
4. Add TestClient coverage for the UI route and source-filtered query request.
5. Update API/security/architecture docs for the local UI.
6. Run `pytest -q` and focused API tests where the environment allows
   TestClient.

## Tests and verification

- `pytest -q`
- `PMA_RUN_API_TESTCLIENT=1 pytest -q tests/test_api.py` when TestClient is not
  blocked by the sandbox.
- `pma api serve --help`

Tests must not require GPU, model servers, Docker, network access, or private
data.

## Privacy and security

The UI is served only by the local FastAPI app. The CLI continues to bind to
`127.0.0.1` by default and reject non-loopback hosts. The UI defaults to
redacted responses. The optional private display toggle only requests private
display; the backend still requires config `log_private_data` before returning
unredacted fields.

The page must not enumerate local source filenames or display private content
outside the existing query response. Evidence rendering uses snippets returned
by the API, not direct DB reads.

## Performance and hardware

No GPU or model-server requirement is added. The UI is one small HTML response
plus one API request per query.

## Rollback

Remove the `/` and `/ui` routes, HTML renderer, tests, and documentation edits.
The Phase 6-A API endpoints remain unaffected.

## Open questions

None blocking. A future UI phase can add a richer browser app or local auth if
the service ever needs non-loopback access.
