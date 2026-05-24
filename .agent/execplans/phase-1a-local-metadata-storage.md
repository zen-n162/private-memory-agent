# ExecPlan: Phase 1-A Local Metadata Storage

## Goal

Create the local structured storage foundation for Private Memory Agent using SQLite. The storage layer should initialize a local database, apply schema migrations, and provide repository classes for basic insert/get/list operations across the core metadata tables.

## Non-goals

- Do not ingest real photos, LINE exports, notes, GPS, OCR, or model outputs.
- Do not implement vector database search or ANN indexes.
- Do not call AI models, face recognition, OCR, captioning, embedding models, or external APIs.
- Do not log private payloads or print stored private text.
- Do not require a persistent local database or real configured source paths in tests.

## Current state

The repository has a Python 3.11 package, local-first config loading, model registry checks, local raw source path support, and CLI doctor/config/model commands. There is no storage package, no migration runner, and `docs/DATA_MODEL.md` only contains placeholder notes. `pytest` currently uses temporary paths for config tests and must continue to avoid real data.

## Proposed design

Use the Python standard library `sqlite3` module with a small migration mechanism. This keeps Phase 1-A simple and avoids adding Alembic before schema churn justifies it. The migration runner will create `schema_migrations` and apply ordered SQL migrations inside transactions.

Add:

- `storage/database.py`: connection helpers and `Storage` wrapper.
- `storage/migrations.py`: versioned migration SQL.
- `storage/repositories.py`: table-specific repositories with basic insert/get/list and soft-exclude helpers.
- `storage/__init__.py`: public exports.

Rows will use ISO-8601 UTC timestamps for `created_at` and `updated_at`. Privacy-safe removal is represented with `is_excluded`, `excluded_at`, and `excluded_reason` fields where appropriate. Repository methods will not log row payloads.

## Data contracts

SQLite tables:

- `schema_migrations`: applied migration versions.
- `source_items`: raw source records keyed by source type and source URI.
- `media_items`: image/video metadata linked to `source_items`.
- `media_annotations`: derived or human annotations linked to media.
- `line_messages`: LINE message metadata and optional local text payload.
- `notes`: notes metadata and optional local note body.
- `entities`: canonical people/places/orgs/topics.
- `events`: timeline events.
- `evidence_links`: links between evidence rows and target rows.
- `embeddings`: embedding metadata and optional serialized vectors, not a vector DB.
- `audit_log`: privacy-safe operation audit entries without private payload logging.

Repository contract:

- `insert(values) -> int`
- `get(row_id, include_excluded=False) -> dict | None`
- `list(limit=100, offset=0, include_excluded=False) -> list[dict]`
- `mark_excluded(row_id, reason=None) -> bool` for tables with `is_excluded`.

## Files to change

- `.agent/execplans/phase-1a-local-metadata-storage.md`
- `docs/DATA_MODEL.md`
- `src/private_memory_agent/storage/__init__.py`
- `src/private_memory_agent/storage/database.py`
- `src/private_memory_agent/storage/migrations.py`
- `src/private_memory_agent/storage/repositories.py`
- `tests/test_storage.py`

## Implementation steps

1. Add migration SQL for all required tables and indexes.
2. Add connection helpers that enable foreign keys and row dictionaries.
3. Add a migration runner that records applied versions.
4. Add repository classes for each table with basic insert/get/list behavior.
5. Add soft-exclude behavior for tables that carry privacy removal flags.
6. Update `docs/DATA_MODEL.md` with table descriptions and privacy fields.
7. Add tests using temporary SQLite databases.
8. Run `pytest -q`.

## Tests and verification

Run:

- `pytest -q`

Tests will create temporary SQLite files under `tmp_path`, apply migrations, insert synthetic non-private rows, validate table existence, validate duplicate constraints, validate repository get/list behavior, and validate soft-exclusion.

## Privacy and security

The storage layer can hold private metadata in later phases, but Phase 1-A tests use synthetic values only. Repositories do not log private payloads. Tables include `is_excluded`, `excluded_at`, and `excluded_reason` where appropriate so future workflows can hide data without destructive deletion.

## Performance and hardware

SQLite metadata operations are CPU-only and lightweight. No GPU, VRAM, model runtime, vector index, or network access is involved.

## Rollback

Remove the new storage package, storage tests, and data model documentation changes. No real database or source data is modified by this implementation.

## Open questions

None blocking for Phase 1-A.
