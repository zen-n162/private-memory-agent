"""Embedding abstractions and lightweight local implementations."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

from private_memory_agent.retrieval.text import index_text, normalize_text
from private_memory_agent.storage import initialize_database


class EmbeddingModel(Protocol):
    """Protocol for future local embedding model adapters."""

    model_id: str
    dimensions: int

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one embedding vector per text."""


@dataclass(frozen=True)
class EmbeddedDocument:
    """A text document and its embedding vector."""

    document_id: int
    source_table: str
    source_id: int
    text: str
    vector: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VectorSearchResult:
    """A vector search hit."""

    document: EmbeddedDocument
    score: float


class VectorStore(Protocol):
    """Protocol for future vector store adapters."""

    def upsert(self, documents: Sequence[EmbeddedDocument]) -> None:
        """Insert or replace embedded documents."""

    def search(self, vector: Sequence[float], *, limit: int = 10) -> list[VectorSearchResult]:
        """Return nearest documents."""


@dataclass(frozen=True)
class EmbeddingIndexResult:
    """Summary of an embedding indexing run."""

    documents_embedded: int
    model_id: str
    dimensions: int


@dataclass(frozen=True)
class SemanticSearchResult:
    """Structured semantic search result."""

    source_table: str
    source_id: int
    title: str | None
    snippet: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_table": self.source_table,
            "source_id": self.source_id,
            "title": self.title,
            "snippet": self.snippet,
            "score": self.score,
        }


class FakeEmbeddingModel:
    """Deterministic token-count embedding model for tests."""

    def __init__(self, vocabulary: Sequence[str] | None = None, *, model_id: str = "fake-embedding-v1") -> None:
        self.vocabulary = tuple(vocabulary or ("ローカル", "こんにちは", "買い物", "研究", "json"))
        self.model_id = model_id
        self.dimensions = len(self.vocabulary)

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            normalized = normalize_text(text)
            vector = [float(normalized.count(token.casefold())) for token in self.vocabulary]
            vectors.append(_l2_normalize(vector))
        return vectors


class HashEmbeddingModel:
    """Deterministic hash-bucket embedding for local dev and tests."""

    def __init__(self, *, dimensions: int = 32, model_id: str = "hash-embedding-v1") -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.dimensions = dimensions
        self.model_id = model_id

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in tokenize_for_hash_embedding(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign
        return _l2_normalize(vector)


class SentenceTransformersEmbeddingModel:
    """Optional local sentence-transformers adapter.

    Heavy libraries are imported only when this class is instantiated and used.
    The model path must already exist; this adapter does not download models.
    """

    def __init__(
        self,
        model_path: Path | str,
        *,
        model_id: str | None = None,
        device: str | None = None,
        batch_size: int = 32,
        normalize_embeddings: bool = True,
    ) -> None:
        self.model_path = Path(model_path).expanduser()
        if not self.model_path.exists():
            raise FileNotFoundError("configured embedding model path does not exist")
        self.model_id = model_id or self.model_path.name
        self.device = device
        self.batch_size = batch_size
        self.normalize_embeddings = normalize_embeddings
        self.dimensions = 0
        self._model: Any | None = None

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._load_model()
        embeddings = model.encode(
            list(texts),
            batch_size=self.batch_size,
            convert_to_numpy=False,
            normalize_embeddings=self.normalize_embeddings,
            show_progress_bar=False,
        )
        vectors = [_vector_to_float_list(vector) for vector in embeddings]
        if vectors:
            self.dimensions = len(vectors[0])
        return vectors

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model

        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError("sentence-transformers is not installed") from exc

        kwargs: dict[str, Any] = {}
        if self.device:
            kwargs["device"] = self.device
        try:
            self._model = SentenceTransformer(str(self.model_path), local_files_only=True, **kwargs)
        except TypeError:
            self._model = SentenceTransformer(str(self.model_path), **kwargs)
        return self._model


class InMemoryVectorStore:
    """Small process-local vector store for tests and development."""

    def __init__(self) -> None:
        self._documents: dict[int, EmbeddedDocument] = {}

    def upsert(self, documents: Sequence[EmbeddedDocument]) -> None:
        for document in documents:
            self._documents[document.document_id] = document

    def search(self, vector: Sequence[float], *, limit: int = 10) -> list[VectorSearchResult]:
        scored = [
            VectorSearchResult(document=document, score=cosine_similarity(vector, document.vector))
            for document in self._documents.values()
        ]
        scored.sort(key=lambda result: (-result.score, result.document.document_id))
        return scored[:limit]

    def __len__(self) -> int:
        return len(self._documents)


class QdrantVectorStore:
    """Optional Qdrant vector store adapter.

    The Qdrant service must already be running. This adapter never starts
    Docker or creates network resources unless explicitly selected.
    """

    def __init__(
        self,
        *,
        collection_name: str,
        vector_size: int | None = None,
        url: str = "http://localhost:6333",
        distance: str = "Cosine",
    ) -> None:
        if vector_size is not None and vector_size <= 0:
            raise ValueError("vector_size must be positive")
        self.collection_name = collection_name
        self.vector_size = vector_size
        self.url = url
        self.distance = distance
        self._client: Any | None = None

    def upsert(self, documents: Sequence[EmbeddedDocument]) -> None:
        if not documents:
            return
        if self.vector_size is None:
            self.vector_size = len(documents[0].vector)
        client, models = self._load_client_and_models()
        self._ensure_collection(client, models)
        points = [
            models.PointStruct(
                id=document.document_id,
                vector=document.vector,
                payload={
                    "source_table": document.source_table,
                    "source_id": document.source_id,
                    "metadata": {
                        "text_search_document_id": document.metadata.get("text_search_document_id"),
                    },
                },
            )
            for document in documents
        ]
        client.upsert(collection_name=self.collection_name, points=points)

    def search(self, vector: Sequence[float], *, limit: int = 10) -> list[VectorSearchResult]:
        client, _models = self._load_client_and_models()
        try:
            response = client.search(
                collection_name=self.collection_name,
                query_vector=list(vector),
                limit=limit,
                with_payload=True,
                with_vectors=True,
            )
        except AttributeError:
            response = client.query_points(
                collection_name=self.collection_name,
                query=list(vector),
                limit=limit,
                with_payload=True,
                with_vectors=True,
            ).points
        results: list[VectorSearchResult] = []
        for point in response:
            payload = point.payload or {}
            metadata = payload.get("metadata") or {}
            document = EmbeddedDocument(
                document_id=int(point.id),
                source_table=str(payload.get("source_table") or ""),
                source_id=int(payload.get("source_id") or 0),
                text="",
                vector=[float(value) for value in (point.vector or [])],
                metadata=metadata if isinstance(metadata, dict) else {},
            )
            results.append(VectorSearchResult(document=document, score=float(point.score)))
        return results

    def _load_client_and_models(self) -> tuple[Any, Any]:
        try:
            from qdrant_client import QdrantClient, models
        except ImportError as exc:
            raise RuntimeError("qdrant-client is not installed") from exc
        if self._client is None:
            self._client = QdrantClient(url=self.url)
        return self._client, models

    def _ensure_collection(self, client: Any, models: Any) -> None:
        distance = getattr(models.Distance, self.distance.upper(), models.Distance.COSINE)
        try:
            client.get_collection(self.collection_name)
        except Exception:
            client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(size=int(self.vector_size), distance=distance),
            )


def index_embeddings(
    db_path: Path | str,
    model: EmbeddingModel,
    *,
    vector_store: VectorStore | None = None,
) -> EmbeddingIndexResult:
    """Embed indexed text documents and persist local embedding records."""

    index_text(db_path)
    storage = initialize_database(db_path)
    try:
        rows = storage.connection.execute(
            """
            SELECT id, source_table, source_id, title, body, snippet_text, normalized_text
            FROM text_search_documents
            WHERE is_excluded = 0
            ORDER BY id
            """,
        ).fetchall()
        texts = [row["normalized_text"] or "" for row in rows]
        vectors = model.embed_texts(texts)
        if any(len(vector) != model.dimensions for vector in vectors):
            raise ValueError("embedding model returned a vector with unexpected dimensions")

        embedded_documents: list[EmbeddedDocument] = []
        with storage.transaction():
            storage.connection.execute(
                "DELETE FROM embeddings WHERE embedding_type = ? AND model_id = ?",
                ("text", model.model_id),
            )
            for row, vector in zip(rows, vectors, strict=True):
                metadata = {
                    "text_search_document_id": int(row["id"]),
                    "title": row["title"],
                    "snippet_text": row["snippet_text"],
                }
                storage.embeddings.insert_embedding(
                    owner_table=str(row["source_table"]),
                    owner_id=int(row["source_id"]),
                    embedding_type="text",
                    model_id=model.model_id,
                    dimensions=model.dimensions,
                    vector_json=json.dumps(vector, sort_keys=True),
                    metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                )
                embedded_documents.append(
                    EmbeddedDocument(
                        document_id=int(row["id"]),
                        source_table=str(row["source_table"]),
                        source_id=int(row["source_id"]),
                        text=row["normalized_text"] or "",
                        vector=list(vector),
                        metadata={
                            "text_search_document_id": int(row["id"]),
                            "title": row["title"],
                            "snippet_text": row["snippet_text"],
                        },
                    ),
                )

        if vector_store is not None:
            vector_store.upsert(embedded_documents)

        return EmbeddingIndexResult(
            documents_embedded=len(embedded_documents),
            model_id=model.model_id,
            dimensions=model.dimensions,
        )
    finally:
        storage.close()


def semantic_search(
    db_path: Path | str,
    query: str,
    model: EmbeddingModel,
    *,
    vector_store: VectorStore | None = None,
    limit: int = 10,
    source_tables: tuple[str, ...] = (),
) -> list[SemanticSearchResult]:
    """Search indexed text documents with lightweight embeddings."""

    normalized_query = normalize_text(query)
    if not normalized_query:
        return []
    store = vector_store or build_in_memory_vector_store(
        db_path,
        model_id=model.model_id,
        source_tables=source_tables,
    )
    query_vector = model.embed_texts([normalized_query])[0]
    hits = store.search(query_vector, limit=limit)
    metadata_by_source = _semantic_metadata_by_source(db_path, hits)
    results: list[SemanticSearchResult] = []
    for hit in hits:
        metadata = metadata_by_source.get((hit.document.source_table, hit.document.source_id), {})
        results.append(
            SemanticSearchResult(
                source_table=hit.document.source_table,
                source_id=hit.document.source_id,
                title=_optional_string(metadata.get("title", hit.document.metadata.get("title"))),
                snippet=_make_semantic_snippet(
                    metadata.get("snippet_text", hit.document.metadata.get("snippet_text")),
                ),
                score=hit.score,
            ),
        )
    return results


def build_in_memory_vector_store(
    db_path: Path | str,
    *,
    model_id: str,
    source_tables: tuple[str, ...] = (),
) -> InMemoryVectorStore:
    """Load persisted embedding rows into an in-memory vector store."""

    storage = initialize_database(db_path)
    store = InMemoryVectorStore()
    try:
        cleaned_source_tables = tuple(dict.fromkeys(table for table in source_tables if table))
        source_sql = ""
        params: list[Any] = [model_id]
        if cleaned_source_tables:
            placeholders = ", ".join("?" for _ in cleaned_source_tables)
            source_sql = f" AND e.owner_table IN ({placeholders})"
            params.extend(cleaned_source_tables)
        rows = storage.connection.execute(
            f"""
            SELECT e.owner_table,
                   e.owner_id,
                   e.vector_json,
                   e.metadata_json,
                   d.id AS document_id,
                   d.normalized_text
            FROM embeddings e
            JOIN text_search_documents d
              ON d.source_table = e.owner_table
             AND d.source_id = e.owner_id
            WHERE e.embedding_type = 'text'
              AND e.model_id = ?
              AND e.is_excluded = 0
              AND d.is_excluded = 0
              {source_sql}
            ORDER BY d.id
            """,
            tuple(params),
        ).fetchall()
        documents: list[EmbeddedDocument] = []
        for row in rows:
            metadata = json.loads(row["metadata_json"] or "{}")
            documents.append(
                EmbeddedDocument(
                    document_id=int(row["document_id"]),
                    source_table=str(row["owner_table"]),
                    source_id=int(row["owner_id"]),
                    text=row["normalized_text"] or "",
                    vector=[float(value) for value in json.loads(row["vector_json"] or "[]")],
                    metadata=metadata,
                ),
            )
        store.upsert(documents)
        return store
    finally:
        storage.close()


def tokenize_for_hash_embedding(text: str) -> list[str]:
    normalized = normalize_text(text)
    tokens = re.findall(r"[a-z0-9]+|[\u3040-\u30ff\u3400-\u9fff]+", normalized)
    if tokens:
        return tokens
    return [char for char in normalized if not char.isspace()]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _l2_normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return [0.0 for _ in vector]
    return [float(value) / norm for value in vector]


def _make_semantic_snippet(value: Any, *, max_chars: int = 96) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars] + "..."


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _semantic_metadata_by_source(
    db_path: Path | str,
    hits: Sequence[VectorSearchResult],
) -> dict[tuple[str, int], dict[str, Any]]:
    if not hits:
        return {}
    storage = initialize_database(db_path)
    try:
        metadata: dict[tuple[str, int], dict[str, Any]] = {}
        for hit in hits:
            row = storage.connection.execute(
                """
                SELECT title, snippet_text
                FROM text_search_documents
                WHERE source_table = ?
                  AND source_id = ?
                  AND is_excluded = 0
                LIMIT 1
                """,
                (hit.document.source_table, hit.document.source_id),
            ).fetchone()
            if row is not None:
                metadata[(hit.document.source_table, hit.document.source_id)] = {
                    "title": row["title"],
                    "snippet_text": row["snippet_text"],
                }
        return metadata
    finally:
        storage.close()


def _vector_to_float_list(vector: Any) -> list[float]:
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    return [float(value) for value in vector]
