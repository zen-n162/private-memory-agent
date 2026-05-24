# ExecPlan: Phase 8-D0 Schema-Aware Retrieval Diagnostics

## Goal

Add privacy-safe schema-aware diagnostics so PMA reports the actual SQLite schema, clarifies physical versus logical index counts, and distinguishes real retrieval evidence from inventory fallback.

## Non-goals

- Do not ingest new data.
- Do not run photo annotation or model inference.
- Do not modify source files.
- Do not print raw LINE text, note bodies, captions, filenames, full paths, GPS, EXIF, OCR, personal names, or local query text.
- Do not assume guessed table or column names such as `text_documents.source_type`.
- Do not require GPU, model servers, Qdrant, network, or private data in tests.

## Current state

Phase 8-C added `pma e2e smoke`, but local inspection showed confusion around index table names:

- PMA reports `text_documents_count`, but SQLite has `text_search_documents`, not `text_documents`.
- `embeddings` stores `owner_table` and `owner_id`, not `source_type`.
- E2E smoke currently lets `inventory_fallback` return evidence even when configured queries return zero evidence.

The repository already has migrations for:

- `line_messages`
- `notes`
- `media_items`
- `media_annotations`
- `embeddings`
- `text_search_documents`
- optional `text_search_fts`

## Proposed design

Add schema diagnostics based on SQLite introspection:

- `pma db schema --config configs/paths.local.yaml`
- JSON and human output with table names, view names, column names, index names, and safe row counts for known tables.

Add retrieval audit:

- `pma retrieve audit --config configs/paths.local.yaml --json`
- reports actual source coverage, physical table availability, index count provenance, media annotation searchability, embedding breakdown availability, and optional query stage summaries.

Clarify E2E smoke output:

- Add `text_documents_count_kind`.
- Add `text_documents_table`.
- Add `text_documents_derived_from`.
- Add `embedding_count_kind`.
- Add `embedding_source_breakdown_available`.
- Add whether media annotations are included in text index and direct retrieval.
- Add `--no-fallback` so inventory fallback never counts as real retrieval success.

## Data contracts

Add dataclasses:

- `DatabaseSchemaReport`
- `SchemaTableInfo`
- `SchemaViewInfo`
- `SchemaIndexInfo`
- `SourceCoverageReport`
- `RetrievalAuditReport`
- `MediaAnnotationDiagnostics`
- `EmbeddingDiagnostics`

Extend E2E dataclasses:

- `E2EIndexStatus`
- `E2ESmokeOptions`
- `E2ESourceCoverage`
- `E2ESmokeReport`

All `to_dict` methods must contain metadata only. No table row payloads.

## Files to change

- `.agent/execplans/phase-8d0-schema-aware-retrieval-diagnostics.md`
- `src/private_memory_agent/db_diagnostics.py`
- `src/private_memory_agent/e2e.py`
- `src/private_memory_agent/cli.py`
- `tests/test_db_diagnostics.py`
- `tests/test_e2e_smoke.py`
- `docs/MODEL_RUNTIME.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add schema/report dataclasses and SQLite inspection helpers.
2. Add source coverage and index provenance diagnostics.
3. Add `pma db schema` CLI command.
4. Add `pma retrieve audit` CLI command.
5. Add E2E `--no-fallback` and clarify index count provenance in output.
6. Add synthetic tests for missing physical tables, embedding source mapping, no-fallback behavior, media annotation diagnostics, and privacy-safe output.
7. Update docs and overview.
8. Run verification commands.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

If local DB exists, run:

```bash
pma stats --config configs/paths.local.yaml
pma db schema --config configs/paths.local.yaml
pma retrieve audit --config configs/paths.local.yaml --json
pma e2e smoke --config configs/paths.local.yaml --retrieval-only --no-fallback --json
```

## Privacy and security

Schema diagnostics show only names of schema objects and aggregate counts. Retrieval diagnostics show only source labels, counts, stage names, evidence ids where already considered safe, and fallback flags. They never read or print private payload fields.

## Performance and hardware

No GPU assumptions. SQLite introspection and count queries are local and bounded to known metadata tables. No model servers or vector stores are started.

## Rollback

Remove the new diagnostics module, CLI command wiring, tests, and documentation updates. Existing database schema is unchanged.

## Open questions

None blocking.
