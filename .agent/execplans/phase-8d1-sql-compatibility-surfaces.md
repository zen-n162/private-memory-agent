# ExecPlan: Phase 8-D1 SQL Compatibility Surfaces

## Goal

Make common manual SQLite inspection queries work without requiring the user to
remember PMA's internal table names. In particular:

- `SELECT source_type, COUNT(*) FROM text_documents GROUP BY source_type;`
- `SELECT source_type, COUNT(*) FROM embeddings GROUP BY source_type;`
- `SELECT COUNT(*) FROM text_documents WHERE source_type LIKE '%photo%' OR source_type LIKE '%media%';`

## Non-goals

- Do not rename canonical PMA tables.
- Do not expose private row payloads in CLI output.
- Do not copy, ingest, move, or modify source photos, LINE exports, or notes.
- Do not require GPU, model servers, network, or real private data in tests.

## Current state

The physical text retrieval table is `text_search_documents`. It has
`source_table` values such as `line_messages`, `notes`, and `media_items`.
There is no physical `text_documents` table.

The `embeddings` table stores `owner_table` and `owner_id`. It does not
currently store a `source_type` column, although diagnostics can derive source
coverage from `owner_table`.

## Proposed design

Add a migration that:

- Creates a compatibility view named `text_documents` over
  `text_search_documents`.
- Adds a derived nullable `source_type` column to `embeddings`.
- Backfills `embeddings.source_type` from `owner_table`.
- Adds an index on `embeddings.source_type`.

Keep canonical code paths using existing tables. The compatibility view is for
manual diagnostics and aggregate checks.

## Data contracts

`text_documents` view columns:

- `id`
- `source_type`
- `source_table`
- `source_id`
- `title`
- `body`
- `normalized_text`
- `snippet_text`
- `is_excluded`
- `indexed_at`

`source_type` mapping:

- `line_messages` -> `line`
- `notes` -> `notes`
- `media_items` -> `photos`
- `media_annotations` -> `photos`

`embeddings.source_type` uses the same mapping.

## Files to change

- `src/private_memory_agent/storage/migrations.py`
- `src/private_memory_agent/storage/repositories.py`
- `tests/test_storage.py`
- `tests/test_db_diagnostics.py`
- `docs/RETRIEVAL.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add migration version 4 for the compatibility view and embedding source
   column.
2. Populate `source_type` during future embedding inserts.
3. Add tests for the manual SQL queries that previously failed.
4. Update docs to mention canonical schema and compatibility surfaces.
5. Run tests and HTML validation.
6. Apply the migration to the local DB by running a safe PMA command.
7. Verify the user's three SQLite queries no longer error.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

If local DB exists, run the three manual aggregate SQLite queries. They must not
print private payloads.

## Privacy and security

The compatibility view exposes the same text fields as the existing local text
index table. It is intended for local manual SQLite inspection only. The app's
privacy-safe CLI commands remain the recommended path. Verification uses
aggregate `COUNT(*)` queries only.

## Performance and hardware

No GPU or model runtime impact. The new index on `embeddings.source_type` is
small relative to vector payloads and only improves aggregate diagnostics.

## Rollback

Drop the `text_documents` view and ignore the nullable
`embeddings.source_type` column. Canonical retrieval continues to use
`text_search_documents` and `owner_table`.

## Open questions

None blocking.
