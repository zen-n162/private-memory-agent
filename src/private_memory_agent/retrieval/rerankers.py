"""Local evidence reranker interfaces.

Real reranker adapters are lazy and local-only. Unit tests use fake or
deterministic rerankers and never load model weights.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from private_memory_agent.retrieval.text import extract_query_terms, normalize_text

RERANKER_MODEL_ALIASES = {
    "ruri-v3-reranker-310m": "text_reranker",
    "qwen3-reranker-0.6b": "text_reranker_qwen_06b",
}
RERANKER_MODEL_CHOICES = ("none", "fake", *RERANKER_MODEL_ALIASES)


class EvidenceReranker(Protocol):
    """Interface for reranking retrieved evidence candidates."""

    model_id: str

    def rerank(
        self,
        query: str,
        evidence: Sequence[Any],
    ) -> list[tuple[Any, float]]:
        """Return evidence paired with reranker scores."""


@dataclass(frozen=True)
class FakeEvidenceReranker:
    """Deterministic reranker for tests and lightweight diagnostics."""

    model_id: str = "fake-reranker-v1"

    def rerank(
        self,
        query: str,
        evidence: Sequence[Any],
    ) -> list[tuple[Any, float]]:
        terms = extract_query_terms(normalize_text(query), max_terms=12)
        scored: list[tuple[Evidence, float]] = []
        for item in evidence:
            text = normalize_text(" ".join(part for part in (item.title, item.snippet) if part))
            hits = sum(1 for term in terms if term and term in text)
            scored.append((item, float(hits) + item.score * 0.01))
        scored.sort(key=lambda pair: (-pair[1], -pair[0].score, pair[0].evidence_id))
        return scored


class SentenceTransformersReranker:
    """Optional local sentence-transformers CrossEncoder reranker."""

    def __init__(
        self,
        model_path: Path | str,
        *,
        model_id: str | None = None,
        device: str | None = None,
    ) -> None:
        self.model_path = Path(model_path).expanduser()
        if not self.model_path.exists():
            raise FileNotFoundError("configured reranker model path does not exist")
        self.model_id = model_id or self.model_path.name
        self.device = device
        self._model: Any | None = None

    def rerank(
        self,
        query: str,
        evidence: Sequence[Any],
    ) -> list[tuple[Any, float]]:
        if not evidence:
            return []
        model = self._load_model()
        pairs = [
            [query, " ".join(part for part in (item.title, item.snippet) if part)]
            for item in evidence
        ]
        raw_scores = model.predict(pairs, show_progress_bar=False)
        scores = [float(score) for score in raw_scores]
        scored = list(zip(evidence, scores, strict=True))
        scored.sort(key=lambda pair: (-pair[1], -pair[0].score, pair[0].evidence_id))
        return [(item, score) for item, score in scored]

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise RuntimeError("sentence-transformers is not installed") from exc
        kwargs: dict[str, Any] = {}
        if self.device:
            kwargs["device"] = self.device
        self._model = CrossEncoder(str(self.model_path), **kwargs)
        return self._model


def rerank_evidence(
    query: str,
    evidence: Sequence[Any],
    reranker: EvidenceReranker,
    *,
    top_k: int,
) -> tuple[Any, ...]:
    """Rerank the top candidates and preserve the rest in original order."""

    if top_k <= 0:
        raise ValueError("rerank_top_k must be positive")
    head = list(evidence[:top_k])
    tail = list(evidence[top_k:])
    reranked = [
        item
        for item, score in reranker.rerank(query, head)
        if score > float("-inf")
    ]
    seen = {item.evidence_id for item in reranked}
    reranked.extend(item for item in tail if item.evidence_id not in seen)
    return tuple(reranked)


def build_evidence_reranker(
    reranker_name: str,
    *,
    config: Any | None = None,
    device: str | None = None,
) -> EvidenceReranker | None:
    """Build a local reranker from a public alias.

    Real rerankers are resolved through model config and loaded lazily. Unit
    tests and default runs use none/fake.
    """

    normalized = normalize_reranker_name(reranker_name)
    if normalized == "none":
        return None
    if normalized == "fake":
        return FakeEvidenceReranker()
    if config is None:
        raise ValueError("config is required for real reranker models")
    model_key = RERANKER_MODEL_ALIASES.get(normalized, normalized)
    model_spec = config.model_registry.get(model_key)
    if model_spec is None:
        raise ValueError(f"configured reranker model key was not found: {model_key}")
    if model_spec.provider != "sentence_transformers":
        raise ValueError("reranker model provider must be sentence_transformers")
    return SentenceTransformersReranker(
        model_spec.resolved_path,
        model_id=normalized,
        device=device,
    )


def normalize_reranker_name(value: str | None) -> str:
    """Normalize public reranker model aliases used by CLI/config."""

    normalized = str(value or "none").strip().lower().replace("_", "-")
    if normalized == "qwen3-reranker-0-6b":
        return "qwen3-reranker-0.6b"
    return normalized
