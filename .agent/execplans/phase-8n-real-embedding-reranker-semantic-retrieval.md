# ExecPlan: Phase 8-N Real Embedding Reranker Semantic Retrieval

## Goal

Enable real local embedding model selection for semantic retrieval and add a
pluggable reranker interface while preserving hash/fake test paths. The user
should be able to build persisted embeddings for selected local sources, choose
configured local embedding models in golden/E2E semantic retrieval, inspect
embedding coverage, and keep all default output privacy-safe.

## Non-goals

- Do not download models.
- Do not load real models in normal unit tests.
- Do not require GPU, Qdrant, Docker, network, or private data in unit tests.
- Do not run real embedding indexing by default.
- Do not print raw LINE text, note bodies, captions, filenames, paths, GPS,
  EXIF, OCR, embedding input text, or model output.

## Current state

Phase 8-M added semantic retrieval to E2E/golden using hash or fake embeddings.
The repository already has `SentenceTransformersEmbeddingModel` with lazy import
and local-files-only behavior, plus configured embedding model entries such as
`text_embedding`, `text_embedding_bge_m3`, and `text_embedding_qwen_06b`.
`pma index embeddings` embeds all `text_search_documents` for one model and
rebuilds by deleting existing records for that model.

## Proposed design

- Add semantic model aliases that resolve to model registry entries:
  `ruri-v3-310m`, `ruri-v3-130m`, `bge-m3`, and `qwen3-embedding-0.6b`.
- Keep `hash` and `fake` as first-class no-model semantic backends.
- Add `--model` to `pma index embeddings` as a user-facing alias for configured
  real embedding models.
- Add source filters and `--skip-existing` to embedding indexing.
- Add embedding diagnostics by `model_id` and selected-model coverage.
- Add a `Reranker` protocol with deterministic fake/hash-safe implementations
  and optional local sentence-transformers cross-encoder adapter.
- Wire optional reranker controls into E2E/golden retrieval without enabling
  real rerankers by default.

## Data contracts

CLI additions:

- `pma index embeddings --model MODEL_ALIAS --source SOURCE --skip-existing`
- `--semantic-model hash|fake|ruri-v3-310m|ruri-v3-130m|bge-m3|qwen3-embedding-0.6b|none`
- `--reranker none|fake|ruri-v3-reranker-310m|qwen3-reranker-0.6b`
- `--rerank-top-k N`

Embedding index result gains:

- source filters used
- skipped existing count
- candidate document count

Reports gain:

- `semantic_embedding_model_id`
- `reranker_model_id`
- `reranked_candidate_count`

## Files to change

- `configs/models.example.yaml`
- `src/private_memory_agent/retrieval/embeddings.py`
- `src/private_memory_agent/retrieval/evidence.py`
- `src/private_memory_agent/e2e.py`
- `src/private_memory_agent/evaluation/golden.py`
- `src/private_memory_agent/cli.py`
- `src/private_memory_agent/db_diagnostics.py`
- tests for embedding retrieval, evidence retrieval, E2E, and golden evaluation
- `docs/RETRIEVAL.md`
- `docs/MODEL_RUNTIME.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Extend embedding indexing with source filters and skip-existing behavior.
2. Add semantic model alias resolution through the configured model registry.
3. Add reranker interfaces and deterministic fake reranker.
4. Wire semantic/reranker options into retrieval, E2E, and golden evaluation.
5. Add embedding diagnostics by model and source coverage.
6. Add synthetic tests that do not load real models.
7. Update documentation and run verification.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

Do not run real embedding indexing by default.

Manual real-model commands after verification:

```bash
pma index embeddings --config configs/paths.local.yaml \
  --model ruri-v3-310m --source line --source notes

pma eval golden --config configs/paths.local.yaml --retrieval-only \
  --query-id qst_preparation \
  --leader-plan \
  --leader-rerank \
  --retrieval-repair 1 \
  --semantic \
  --semantic-model ruri-v3-310m \
  --json
```

## Privacy and security

All new output is count- and id-oriented. The embedding and reranker adapters
operate on local metadata/index content only and are instantiated only through
explicit CLI options. Raw local text is not printed.

## Performance and hardware

Real sentence-transformers embedding/reranking may be CPU or GPU backed,
depending on the local library setup and optional `--device`. Unit tests use
fake/hash paths only. The RTX 4500 Ada 24GB target is sufficient for the listed
small embedding/reranker models, but the app never starts or downloads them
automatically.

## Rollback

Revert the listed files. Existing hash/fake semantic retrieval and text
retrieval remain available without real-model aliases or reranker controls.

## Open questions

None blocking.
