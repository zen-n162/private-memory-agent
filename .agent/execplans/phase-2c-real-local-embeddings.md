# ExecPlan: Phase 2-C Real Local Text Embeddings and Pluggable Vector Store

## Goal

Enable optional real local text embeddings through configured model paths while preserving lightweight default tests. Add a lazy SentenceTransformers adapter, optional Qdrant vector store adapter, `pma index embeddings`, and `pma search semantic "query"`.

## Non-goals

- Do not download models.
- Do not load real models during normal unit tests.
- Do not start Docker or Qdrant automatically.
- Do not require GPU, network, sentence-transformers, or qdrant-client for default tests.
- Do not remove the fake/hash embedding implementations.
- Do not implement production vector DB operations beyond the optional adapter boundary.

## Current state

Phase 2-B added `EmbeddingModel`, `VectorStore`, `FakeEmbeddingModel`, `HashEmbeddingModel`, `InMemoryVectorStore`, embedding persistence in SQLite, and semantic search helpers. The CLI has `pma index text` and `pma search text`, but not embedding index/search commands. Config has `text_embedding` for `embedding/ruri-v3-310m`, but not the other requested candidate embedding paths.

## Proposed design

Add optional adapters to `private_memory_agent.retrieval.embeddings`:

- `SentenceTransformersEmbeddingModel`: lazy imports `sentence_transformers`, validates the configured local model path exists, sets offline environment defaults, and loads locally only.
- `QdrantVectorStore`: lazy imports `qdrant_client`, connects only when explicitly selected, and upserts/searches vectors with source identifiers rather than full text payloads.

Keep default CLI backend as `hash`, so commands are runnable without heavy dependencies. Real model use requires explicit `--model-backend sentence-transformers` and either `--model-key` from config or `--model-path`.

CLI:

- `pma index embeddings`
- `pma search semantic "query"`

Both commands support:

- `--db`
- `--model-backend hash|fake|sentence-transformers`
- `--model-key`
- `--model-path`
- `--vector-store memory|qdrant`
- Qdrant connection options only used when selected.

## Data contracts

`SentenceTransformersEmbeddingModel`:

- `model_id`
- `model_path`
- `device`
- `dimensions`
- `embed_texts(texts)`

`QdrantVectorStore`:

- `collection_name`
- `url`
- `vector_size`
- `upsert(documents)`
- `search(vector, limit)`

CLI result payloads remain summary/structured and do not print private full text or source paths.

## Files to change

- `.agent/execplans/phase-2c-real-local-embeddings.md`
- `configs/models.example.yaml`
- `docs/MODEL_RUNTIME.md`
- `docs/RETRIEVAL.md`
- `pyproject.toml`
- `src/private_memory_agent/cli.py`
- `src/private_memory_agent/retrieval/__init__.py`
- `src/private_memory_agent/retrieval/embeddings.py`
- `tests/test_embedding_retrieval.py`
- `tests/test_real_embedding_integration.py`

## Implementation steps

1. Add configured model entries for `embedding/bge-m3` and `qwen/Qwen3-Embedding-0.6B`.
2. Add lazy SentenceTransformers embedding adapter.
3. Add lazy Qdrant vector store adapter.
4. Add CLI factories for model backend and vector store selection.
5. Add `pma index embeddings` and `pma search semantic`.
6. Add default unit tests using hash/fake backends only.
7. Add integration tests marked and skipped unless explicitly enabled by environment variables.
8. Document real model and Qdrant enablement.
9. Run `pytest -q`.

## Tests and verification

Run:

- `pytest -q`

Optional integration tests:

- `PMA_RUN_REAL_EMBEDDING_TESTS=1 PMA_REAL_EMBEDDING_MODEL_PATH=/path/to/model pytest -q -m real_embeddings`
- `PMA_RUN_QDRANT_TESTS=1 PMA_QDRANT_URL=http://localhost:6333 pytest -q -m qdrant`

Default tests must pass without model files, qdrant-client, sentence-transformers, GPU, Docker, or network.

## Privacy and security

No private data is embedded in tests. CLI output is summary or structured search hits with clipped snippets. The SentenceTransformers adapter sets offline environment defaults and rejects missing model paths instead of downloading.

## Performance and hardware

Default hash/fake backends are CPU-only. Real model performance depends on the selected local model and device. Qdrant is optional and must be started manually by the user.

## Rollback

Remove the adapters, CLI commands, tests, config entries, and docs additions. Existing SQLite embedding rows are local artifacts and can be regenerated.

## Open questions

None blocking.
