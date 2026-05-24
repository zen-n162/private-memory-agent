# ExecPlan: Phase 2-B Embedding Interface and Fake Embeddings

## Goal

Create lightweight embedding and vector store abstractions that can support future real local models while keeping current tests deterministic and dependency-free. Provide fake and hash-based embedding implementations, an in-memory vector store, storage records for embeddings, and tests for embedding indexing plus semantic search.

## Non-goals

- Do not load sentence-transformers, Qwen, or any real embedding model.
- Do not require Qdrant, FAISS, GPU, network, or model files.
- Do not expose a production vector database.
- Do not embed real personal data in tests.
- Do not add LLM calls or answer generation.

## Current state

Phase 2-A added local text retrieval over `line_messages` and `notes`, with `text_search_documents` as a deterministic text index and optional SQLite FTS5. The schema already has an `embeddings` table with `owner_table`, `owner_id`, `embedding_type`, `model_id`, `dimensions`, and `vector_json`, but no embedding interfaces, vector store, or semantic search helpers.

## Proposed design

Add `private_memory_agent.retrieval.embeddings` with:

- `EmbeddingModel` protocol.
- `VectorStore` protocol.
- `EmbeddedDocument` and `VectorSearchResult` dataclasses.
- `FakeEmbeddingModel` for tests using configurable token dimensions.
- `HashEmbeddingModel` deterministic local fallback for dev/tests.
- `InMemoryVectorStore` with cosine similarity.
- `index_embeddings(db_path, model, vector_store=None)` to embed `text_search_documents`, persist rows in `embeddings`, and optionally populate a vector store.
- `semantic_search(db_path, query, model, vector_store=None)` to search embedded documents.

The embedding indexer will require or build the Phase 2-A text index first, then persist vectors as JSON in `embeddings`. The in-memory vector store is intentionally process-local and test-focused.

## Data contracts

`EmbeddingModel`:

- `model_id: str`
- `dimensions: int`
- `embed_texts(texts: Sequence[str]) -> list[list[float]]`

`VectorStore`:

- `upsert(documents: Sequence[EmbeddedDocument]) -> None`
- `search(vector: Sequence[float], limit: int = 10) -> list[VectorSearchResult]`

`EmbeddedDocument`:

- `document_id`
- `source_table`
- `source_id`
- `text`
- `vector`
- `metadata`

`EmbeddingIndexResult`:

- `documents_embedded`
- `model_id`
- `dimensions`

## Files to change

- `.agent/execplans/phase-2b-embedding-interface.md`
- `docs/RETRIEVAL.md`
- `src/private_memory_agent/retrieval/__init__.py`
- `src/private_memory_agent/retrieval/embeddings.py`
- `tests/test_embedding_retrieval.py`

## Implementation steps

1. Add embedding and vector store protocols.
2. Add fake and hash embedding implementations.
3. Add in-memory vector store with cosine similarity.
4. Add helpers to collect indexed text documents and persist embedding records.
5. Add semantic search helpers.
6. Add deterministic tests using artificial data and temp SQLite DBs.
7. Update retrieval docs.
8. Run `pytest -q`.

## Tests and verification

Run:

- `pytest -q`

Tests should verify protocol behavior, fake embedding dimensions, hash determinism, in-memory similarity ranking, persisted `embeddings` rows, and semantic search over artificial LINE/notes content.

## Privacy and security

Embedding tests use artificial fixtures only. No raw private data or real model paths are used. The implementation does not log embedded text or vectors. Persisted vectors are local SQLite rows only.

## Performance and hardware

All implementations are CPU-only, small, and deterministic. No GPU/VRAM assumptions apply in this phase.

## Rollback

Remove the embeddings retrieval module, tests, docs additions, and exports. Existing storage tables remain unchanged and no source data is modified.

## Open questions

None blocking.
