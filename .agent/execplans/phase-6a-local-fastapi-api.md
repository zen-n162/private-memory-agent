# ExecPlan: Phase 6-A Local FastAPI API

## Goal

Expose core Private Memory Agent functionality through a localhost-only FastAPI
app and add `pma api serve` for local use.

## Non-goals

- Do not add public hosting or cloud deployment.
- Do not add authentication beyond the localhost-only assumption.
- Do not require real model servers, GPU, Docker, network, or private data in
  tests.
- Do not expose raw LINE text, note bodies, filenames, GPS, names, or private
  paths in API responses by default.
- Do not implement a frontend.

## Current state

The project has CLI commands and service modules for query, ingestion, events,
and entities. There is no API package. FastAPI and uvicorn are available in the
current environment, but the package metadata currently has no runtime
dependencies.

Existing services to reuse:

- `run_query_flow`
- `ingest_photos`
- `ingest_line_exports`
- `ingest_notes`
- `list_events`
- `list_entities`

## Proposed design

Create `private_memory_agent.api` with:

- `create_app(...)`: FastAPI app factory for tests and CLI serving.
- Pydantic request/response schemas.
- Endpoints under `/api`.

API endpoints:

- `GET /api/health`
- `POST /api/query`
- `POST /api/ingest/photos`
- `POST /api/ingest/line`
- `POST /api/ingest/notes`
- `GET /api/events`
- `GET /api/entities`

The app stores default `db_path`, `config_dir`, and `paths_config` in app state.
Requests can override `db_path` for tests and local tooling.

`pma api serve` lazily imports uvicorn and refuses non-loopback hosts because the
phase has no authentication. Default host is `127.0.0.1`.

## Data contracts

Pydantic schemas:

- `HealthResponse`
- `QueryRequest`, `QueryResponse`
- `IngestPhotosRequest`, `IngestPhotosResponse`
- `IngestLineRequest`, `IngestLineResponse`
- `IngestNotesRequest`, `IngestNotesResponse`
- `EventsResponse`
- `EntitiesResponse`
- `ErrorResponse`

Requests that can touch local source paths require explicit `path`. Ingest
responses are count-only.

Query uses the fake leader client by default for API safety and testability.
OpenAI-compatible local model endpoints can be requested later if configured,
but remote URLs remain disallowed by the existing runtime client.

## Files to change

- `.agent/execplans/phase-6a-local-fastapi-api.md`
- `pyproject.toml`
- `src/private_memory_agent/api/__init__.py`
- `src/private_memory_agent/api/app.py`
- `src/private_memory_agent/api/schemas.py`
- `src/private_memory_agent/cli.py`
- `tests/test_api.py`
- `docs/API.md`
- `docs/ARCHITECTURE.md`
- `docs/SECURITY_PRIVACY.md`

## Implementation steps

1. Add API schemas.
2. Add FastAPI app factory and route handlers using existing services.
3. Add `pma api serve` with loopback-only host validation.
4. Add FastAPI TestClient tests using temporary SQLite DBs and synthetic files.
5. Update docs for localhost-only assumption and privacy defaults.
6. Run `pytest -q` and CLI help smoke checks.

## Tests and verification

- `pytest -q`
- `pma api serve --help`
- Optional import/compile check for the API package.

API tests must use temporary paths and synthetic fixtures only. They must not
require model files, model servers, GPU, Docker, network access, or private data.

## Privacy and security

The API is local-only and unauthenticated in Phase 6-A. `pma api serve` binds to
`127.0.0.1` by default and rejects non-loopback hosts. Responses are redacted by
default unless a request asks to show private data and config also enables
`log_private_data`. Ingest endpoints return count-only summaries.

## Performance and hardware

The API layer is a thin local wrapper around existing services. It does not add
GPU or VRAM requirements. Default hardware assumptions remain unchanged.

## Rollback

Remove the API package, CLI `api serve` command, tests, docs, and dependency
metadata additions. Existing CLI and service layers remain intact.

## Open questions

None blocking. A future phase should add local authentication or a token if the
API is ever exposed beyond loopback.
