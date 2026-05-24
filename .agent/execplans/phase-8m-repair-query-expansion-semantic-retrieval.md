# ExecPlan: Phase 8-M Repair Query Expansion And Semantic Retrieval

## Goal

Help leader-guided retrieval repair find specific evidence after generic-only
candidate retrieval. Add privacy-safe semantic retrieval controls to the E2E and
golden evaluation paths, and improve repair query diagnostics so reports show
whether repair uses specific plan concepts rather than generic terms.

## Non-goals

- Do not add new real embedding model loading by default.
- Do not require Qdrant, GPU, network, Docker, or a running model server in unit
  tests.
- Do not hard-code QST-specific or interview-specific retrieval rules.
- Do not print private question text, repair query text, LINE messages, note
  bodies, captions, filenames, paths, GPS, EXIF, OCR, or raw model output by
  default.

## Current state

Phase 8-L separates candidate retrieval from usable evidence. A local
qst_preparation run correctly reports candidate retrieval success but zero
usable evidence because all candidates are generic. Repair is attempted but does
not improve evidence. `RetrievalService` already supports semantic retrieval
when an embedding model is supplied, but E2E/golden smoke paths instantiate it
without an embedding model and warn that embeddings are not enabled.

## Proposed design

- Add semantic retrieval controls to `E2ESmokeOptions` and `GoldenEvalOptions`.
- Use hash embeddings for `--semantic` by default, with `--no-semantic` keeping
  the current behavior.
- Add `semantic_top_k` and `semantic_weight` to retrieval filters.
- Let semantic search source-filter persisted embeddings before vector search so
  source constraints are respected.
- Record semantic candidate counts in retrieval diagnostics without exposing
  snippets.
- Generate repair diagnostics from `RetrievalPlan`:
  specific query count, generic query count, whether specific concepts and main
  entities were used, and total deduplicated repair query count.
- Keep raw repair queries hidden unless existing `--show-plan` exposes the plan.

## Data contracts

New CLI options:

- `--semantic`
- `--no-semantic`
- `--semantic-model hash|fake`
- `--semantic-top-k N`
- `--semantic-weight FLOAT`

New report fields:

- `semantic_enabled`
- `semantic_model`
- `semantic_top_k`
- `semantic_weight`
- `semantic_candidate_count`
- `repair_specific_query_count`
- `repair_generic_query_count`
- `repair_used_specific_concepts`
- `repair_used_main_entities`

## Files to change

- `src/private_memory_agent/retrieval/embeddings.py`
- `src/private_memory_agent/retrieval/evidence.py`
- `src/private_memory_agent/e2e.py`
- `src/private_memory_agent/evaluation/golden.py`
- `src/private_memory_agent/cli.py`
- `tests/test_evidence_retrieval.py`
- `tests/test_golden_evaluation.py`
- `docs/RETRIEVAL.md`
- `docs/MODEL_RUNTIME.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add source-filtered semantic search and retrieval filter controls.
2. Wire semantic embedding model construction into E2E and golden evaluation.
3. Add semantic counts to retrieval/E2E/golden JSON and Markdown output.
4. Improve repair query diagnostics using plan specific concepts/main entities
   and generic concepts.
5. Add CLI flags for E2E and golden evaluation.
6. Add synthetic tests for semantic merge, source filtering, repair query
   diagnostics, and semantic repair improvement.
7. Update documentation and run verification.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

If the local DB exists, run:

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only \
  --query-id qst_preparation \
  --leader-plan \
  --leader-rerank \
  --retrieval-repair 1 \
  --semantic \
  --show-relevance \
  --json

pma eval golden --config configs/paths.local.yaml --retrieval-only \
  --query-id qst_preparation \
  --leader-plan \
  --leader-rerank \
  --retrieval-repair 1 \
  --semantic \
  --minimum-relevance-score 0.6 \
  --require-usable-evidence \
  --relevance-policy strict \
  --json
```

## Privacy and security

Only counts, booleans, source labels, and privacy-safe evidence IDs are printed.
Semantic search runs locally over persisted local embeddings. No source files are
modified. Raw repair query text remains hidden by default.

## Performance and hardware

Default semantic mode uses local hash embeddings and in-memory vector search. It
does not require GPU. It may load persisted embeddings into memory; callers can
control work with `--semantic-top-k` and query limits.

## Rollback

Revert this phase's edits. Existing text/LIKE/FTS retrieval and Phase 8-L
acceptance policy remain intact without semantic controls.

## Open questions

None blocking.
