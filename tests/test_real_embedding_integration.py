import os
import uuid

import pytest

from private_memory_agent.retrieval.embeddings import (
    EmbeddedDocument,
    HashEmbeddingModel,
    QdrantVectorStore,
    SentenceTransformersEmbeddingModel,
)


@pytest.mark.real_embeddings
def test_sentence_transformers_local_model_when_enabled():
    if os.environ.get("PMA_RUN_REAL_EMBEDDING_TESTS") != "1":
        pytest.skip("set PMA_RUN_REAL_EMBEDDING_TESTS=1 to run real embedding tests")
    model_path = os.environ.get("PMA_REAL_EMBEDDING_MODEL_PATH")
    if not model_path:
        pytest.skip("set PMA_REAL_EMBEDDING_MODEL_PATH to a local model directory")

    model = SentenceTransformersEmbeddingModel(model_path)
    vectors = model.embed_texts(["これはローカル埋め込みのテストです。"])

    assert len(vectors) == 1
    assert model.dimensions == len(vectors[0])
    assert model.dimensions > 0


@pytest.mark.qdrant
def test_qdrant_vector_store_when_enabled():
    if os.environ.get("PMA_RUN_QDRANT_TESTS") != "1":
        pytest.skip("set PMA_RUN_QDRANT_TESTS=1 to run Qdrant tests")

    model = HashEmbeddingModel(dimensions=8)
    vector = model.embed_texts(["qdrant integration test"])[0]
    store = QdrantVectorStore(
        collection_name=f"pma_test_{uuid.uuid4().hex}",
        url=os.environ.get("PMA_QDRANT_URL", "http://localhost:6333"),
        vector_size=8,
    )
    store.upsert(
        [
            EmbeddedDocument(
                document_id=1,
                source_table="notes",
                source_id=1,
                text="qdrant integration test",
                vector=vector,
                metadata={"title": "synthetic"},
            ),
        ],
    )
    results = store.search(vector, limit=1)

    assert results
    assert results[0].document.source_table == "notes"
