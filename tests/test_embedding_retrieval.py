import json
from pathlib import Path

from private_memory_agent.cli import main
from private_memory_agent.ingestion.line import ingest_line_exports
from private_memory_agent.ingestion.notes import ingest_notes
from private_memory_agent.retrieval import index_text
from private_memory_agent.retrieval.embeddings import (
    EmbeddedDocument,
    FakeEmbeddingModel,
    HashEmbeddingModel,
    InMemoryVectorStore,
    SentenceTransformersEmbeddingModel,
    build_in_memory_vector_store,
    cosine_similarity,
    index_embeddings,
    semantic_search,
)
from private_memory_agent.storage import initialize_database


FIXTURE_DIR = Path(__file__).parent / "fixtures"
LINE_FIXTURE = FIXTURE_DIR / "line_export_japanese.txt"
NOTES_FIXTURE_DIR = FIXTURE_DIR / "notes"


def seed_database(db_path: Path) -> None:
    ingest_line_exports(LINE_FIXTURE, db_path=db_path)
    ingest_notes(NOTES_FIXTURE_DIR, db_path=db_path)
    index_text(db_path)


def test_fake_embedding_model_is_deterministic_and_dimensioned():
    model = FakeEmbeddingModel(vocabulary=("ローカル", "買い物", "こんにちは"))

    first = model.embed_texts(["ローカル ローカル 買い物"])[0]
    second = model.embed_texts(["ローカル ローカル 買い物"])[0]

    assert model.dimensions == 3
    assert first == second
    assert first[0] > first[1] > first[2]


def test_hash_embedding_model_is_deterministic():
    model = HashEmbeddingModel(dimensions=8)

    first = model.embed_texts(["決定的なテキスト"])[0]
    second = model.embed_texts(["決定的なテキスト"])[0]

    assert len(first) == 8
    assert first == second


def test_in_memory_vector_store_ranks_by_cosine_similarity():
    store = InMemoryVectorStore()
    store.upsert(
        [
            EmbeddedDocument(
                document_id=1,
                source_table="notes",
                source_id=10,
                text="a",
                vector=[1.0, 0.0],
            ),
            EmbeddedDocument(
                document_id=2,
                source_table="line_messages",
                source_id=20,
                text="b",
                vector=[0.0, 1.0],
            ),
        ],
    )

    results = store.search([1.0, 0.0], limit=2)

    assert len(store) == 2
    assert results[0].document.document_id == 1
    assert results[0].score == 1.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_index_embeddings_persists_embedding_records(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    seed_database(db_path)
    model = FakeEmbeddingModel(vocabulary=("ローカル", "こんにちは", "買い物"))
    store = InMemoryVectorStore()

    result = index_embeddings(db_path, model, vector_store=store)

    storage = initialize_database(db_path)
    try:
        rows = storage.connection.execute(
            "SELECT owner_table, owner_id, model_id, dimensions, vector_json, metadata_json FROM embeddings",
        ).fetchall()

        assert result.documents_embedded == len(store)
        assert result.documents_embedded == len(rows)
        assert result.model_id == model.model_id
        assert result.dimensions == 3
        assert rows
        first_vector = json.loads(rows[0]["vector_json"])
        assert len(first_vector) == 3
        assert rows[0]["model_id"] == model.model_id
        assert json.loads(rows[0]["metadata_json"])["text_search_document_id"] >= 1
    finally:
        storage.close()


def test_semantic_search_with_fake_embeddings_finds_japanese_note(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    seed_database(db_path)
    model = FakeEmbeddingModel(vocabulary=("ローカル", "こんにちは", "買い物"))
    index_embeddings(db_path, model)

    results = semantic_search(db_path, "ローカル", model, limit=3)

    assert results
    assert results[0].source_table == "notes"
    assert "ローカル" in results[0].snippet
    assert results[0].score > 0.0


def test_semantic_search_can_use_supplied_vector_store(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    seed_database(db_path)
    model = FakeEmbeddingModel(vocabulary=("ローカル", "こんにちは", "買い物"))
    store = InMemoryVectorStore()
    index_embeddings(db_path, model, vector_store=store)

    results = semantic_search(db_path, "こんにちは", model, vector_store=store, limit=2)

    assert results
    assert results[0].source_table == "line_messages"
    assert "こんにちは" in results[0].snippet


def test_build_in_memory_vector_store_from_persisted_embeddings(tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    seed_database(db_path)
    model = FakeEmbeddingModel(vocabulary=("ローカル", "こんにちは", "買い物"))
    index_embeddings(db_path, model)

    store = build_in_memory_vector_store(db_path, model_id=model.model_id)

    assert len(store) > 0


def test_sentence_transformers_adapter_rejects_missing_path_before_heavy_import(tmp_path):
    missing = tmp_path / "missing-model"

    try:
        SentenceTransformersEmbeddingModel(missing)
    except FileNotFoundError as exc:
        assert "model path does not exist" in str(exc)
    else:
        raise AssertionError("missing model path should fail")


def test_cli_index_embeddings_and_semantic_search_with_hash_backend(capsys, tmp_path):
    db_path = tmp_path / "metadata.sqlite3"
    seed_database(db_path)

    index_exit = main(["index", "embeddings", "--db", str(db_path), "--model-backend", "hash"])
    index_output = capsys.readouterr().out
    search_exit = main(
        [
            "search",
            "semantic",
            "こんにちは",
            "--db",
            str(db_path),
            "--model-backend",
            "hash",
            "--limit",
            "2",
        ],
    )
    search_output = capsys.readouterr().out

    payload = json.loads(search_output)
    assert index_exit == 0
    assert "Embedding index complete" in index_output
    assert "model_id=hash-embedding-v1" in index_output
    assert search_exit == 0
    assert payload["query"] == "こんにちは"
    assert payload["results"]
    assert payload["results"][0]["source_table"] in {"line_messages", "notes"}
