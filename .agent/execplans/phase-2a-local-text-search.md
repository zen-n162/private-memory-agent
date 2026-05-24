# ExecPlan: Phase 2-A Local Text Search

## Goal

Enable local text retrieval over imported LINE messages and notes without embeddings or LLM calls. Add `pma index text` to build a text index and `pma search text "query"` to return structured results with source ids and privacy-safe snippets.

## Non-goals

- Do not call LLMs, embedding models, rerankers, remote APIs, or vector databases.
- Do not use real personal data in tests.
- Do not print full LINE messages or note bodies by default.
- Do not implement semantic search, summarization, or answer generation.
- Do not require Japanese morphological tokenizer dependencies.

## Current state

SQLite storage exists with migrated metadata tables. LINE ingestion stores message text in `line_messages.body_text`, and notes ingestion stores note text in `notes.title` and `notes.body_text`. There is no text index table, no retrieval module, and no `index` or `search` CLI commands.

## Proposed design

Add a second SQLite migration that creates a deterministic text index table:

- `text_search_documents`: source table/id, title, body, normalized text, snippet text, exclusion flag, indexed timestamp.

Also add `normalized_text` columns to `line_messages` and `notes` for future direct filtering and debugging.

The indexer will:

1. Read non-excluded rows from `line_messages` and `notes`.
2. Normalize text with standard-library Unicode normalization and whitespace collapsing.
3. Rebuild `text_search_documents`.
4. Attempt to create and populate an SQLite FTS5 table, `text_search_fts`, when the local SQLite build supports FTS5.

Search will:

- Query FTS5 when available.
- Always query the deterministic normalized table with `LIKE` as a fallback and for Japanese substring matching.
- Deduplicate results.
- Return structured records containing `source_table`, `source_id`, `title`, and a clipped snippet.

## Data contracts

`text_search_documents`:

- `id`
- `source_table`: `line_messages` or `notes`
- `source_id`
- `title`
- `body`
- `normalized_text`
- `snippet_text`
- `is_excluded`
- `indexed_at`

`TextIndexResult`:

- `documents_indexed`
- `fts5_enabled`

`TextSearchResult`:

- `source_table`
- `source_id`
- `title`
- `snippet`
- `score`

## Files to change

- `.agent/execplans/phase-2a-local-text-search.md`
- `docs/RETRIEVAL.md`
- `src/private_memory_agent/cli.py`
- `src/private_memory_agent/retrieval/__init__.py`
- `src/private_memory_agent/retrieval/text.py`
- `src/private_memory_agent/storage/migrations.py`
- `src/private_memory_agent/storage/repositories.py`
- `tests/test_storage.py`
- `tests/test_text_retrieval.py`
- `tests/test_cli.py`

## Implementation steps

1. Add migration v2 for normalized text columns and `text_search_documents`.
2. Add retrieval module with index rebuild and search functions.
3. Add FTS5 setup/population with graceful fallback when unavailable.
4. Add CLI commands `pma index text` and `pma search text "query"`.
5. Add deterministic Japanese retrieval tests using artificial LINE and notes fixtures.
6. Add retrieval documentation.
7. Run `pytest -q`.

## Tests and verification

Run:

- `pytest -q`

Tests use temporary SQLite databases and artificial fixtures only. They verify indexing, Japanese note search, Japanese LINE search, snippets, CLI JSON output, and migration versioning.

## Privacy and security

Snippets are clipped, whitespace-normalized, and returned only for explicit search commands. The CLI does not print source paths, filenames, full note bodies, or full LINE exports. Indexing output is count-only.

## Performance and hardware

This is CPU-only SQLite retrieval. No GPU, VRAM, models, vector index, or network access is used.

## Rollback

Remove the retrieval module, CLI commands, migration v2 additions, tests, and docs. Existing source data is not modified; the text index can be rebuilt from `line_messages` and `notes`.

## Open questions

None blocking.
