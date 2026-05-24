# ExecPlan: Phase 8-D Retrieval Diagnosis And Real Evidence Recovery

## Goal

Make E2E smoke distinguish real retrieval from inventory fallback, add privacy-safe retrieval diagnostics, and improve local retrieval so Japanese smoke queries can recover real photo, LINE, and notes evidence without printing private content.

## Non-goals

- Do not improve final answer quality.
- Do not run model inference or photo annotation.
- Do not ingest or mutate source files.
- Do not print raw LINE text, note bodies, image captions, filenames, paths, GPS, EXIF, OCR, personal names, or local query text.
- Do not require GPU, model servers, Qdrant, network, or private data in tests.

## Current state

Phase 8-C added `pma e2e smoke`. It reports database/index counts and can run retrieval plus fake answer generation. The command appends an `inventory_fallback` query when configured queries return no evidence. Local validation showed that all main smoke queries had `evidence_count=0`, while only `inventory_fallback` returned photo evidence. That means fallback was masking real retrieval failure.

Phase 8-D0 added schema-aware diagnostics and confirmed that the actual SQLite
text index table is `text_search_documents`, not `text_documents`. It also
confirmed that local embeddings are tied to `owner_table` and currently cover
LINE and notes, while photo annotations are not yet included in the text index.

Current retrieval behavior:

- `index_text` indexes LINE and notes only.
- `RetrievalService` can search LINE/notes via text search and photos via direct media annotation scanning.
- Text search uses FTS plus exact normalized phrase LIKE; Japanese full-sentence queries often fail because the exact phrase is not present.
- Embeddings may exist in the DB, but E2E smoke does not enable semantic retrieval by default.

## Proposed design

Add:

- E2E `--diagnose` output with per-query stage counts.
- E2E `--no-fallback` to prevent inventory fallback from affecting status.
- Source coverage summary in smoke reports.
- Photo annotation indexing into `text_search_documents` as `source_table='media_items'`.
- Japanese keyword LIKE fallback for text search.
- Retrieval diagnostics that count FTS, keyword LIKE, semantic, media annotation, post-filter, post-ranking, and final candidates.
- A warning when embeddings exist but semantic retrieval is not enabled.

The default smoke command may still display fallback evidence as a clearly marked diagnostic aid, but fallback evidence does not count as real retrieval success.

This implementation must keep all diagnostics aggregate-only. It can write
derived search index rows to the local SQLite database via explicit `pma index
text`, but it must not modify, move, or copy source photos, LINE exports, or
notes.

## Data contracts

Add dataclasses:

- `TextSearchDiagnostics`
- `RetrievalDiagnostics`
- `E2ESourceCoverage`

Extend:

- `E2ESmokeOptions`: `diagnose`, `no_fallback`
- `E2ESmokeQueryResult`: `diagnostics`, `evidence_source_counts`, `fallback_reason`
- `E2ESmokeReport`: `source_coverage`

All `to_dict` methods must exclude query text and snippets.

## Files to change

- `.agent/execplans/phase-8d-retrieval-diagnosis-real-evidence-recovery.md`
- `src/private_memory_agent/retrieval/text.py`
- `src/private_memory_agent/retrieval/evidence.py`
- `src/private_memory_agent/retrieval/__init__.py`
- `src/private_memory_agent/e2e.py`
- `src/private_memory_agent/cli.py`
- `configs/e2e_smoke_queries.example.yaml`
- `tests/test_e2e_smoke.py`
- `tests/test_text_retrieval.py`
- `docs/MODEL_RUNTIME.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add media annotation documents to text indexing.
2. Add Japanese keyword LIKE fallback and text-search diagnostics.
3. Add retrieval-stage diagnostics in `RetrievalService`.
4. Add E2E `--diagnose`, `--no-fallback`, semantic warning, and source coverage summary.
5. Add CLI parser options and privacy-safe formatting.
6. Update query profile comments with safe local query guidance.
7. Add synthetic tests for diagnostics, no-fallback, media annotation indexing, source filters, Japanese substring fallback, and privacy-safe output.
8. Update docs and overview.
9. Run verification commands and local smoke/audit commands without printing private payloads.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

If local DB exists, run:

```bash
pma e2e smoke --config configs/paths.local.yaml --retrieval-only --json
pma e2e smoke --config configs/paths.local.yaml --retrieval-only --no-fallback --json
pma e2e smoke --config configs/paths.local.yaml --retrieval-only --diagnose --json
```

## Privacy and security

Diagnostics report counts, source labels, evidence IDs, and safe stage names only. Query text and snippets are not printed. Fallback must be explicitly marked. Source files remain read-only and untouched.

## Performance and hardware

No GPU assumptions. Keyword fallback uses bounded LIKE searches over the local text index; it is appropriate for smoke diagnostics and small result limits. It does not start external vector stores or model servers.

## Rollback

Remove the new diagnostics dataclasses and CLI options, restore `index_text` to LINE/notes-only documents, and remove documentation/test updates. Existing DB rows are regenerated by `pma index text`; no schema rollback is required.

## Open questions

None blocking.
