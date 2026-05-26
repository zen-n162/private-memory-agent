"""Pydantic schemas for the local FastAPI API."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SourceKind = Literal["photos", "line", "notes"]
SemanticModel = Literal["none", "hash", "fake"]
LeaderClient = Literal["fake", "openai-compatible"]
EntityType = Literal["person", "place", "organization", "topic"]
ChatConsoleMode = Literal["retrieval-only", "fake-model", "real-model"]
ChatConsoleResponseMode = Literal["retrieval-only", "fake-model", "real-model", "unknown"]
ChatConsoleSemanticModel = Literal[
    "none",
    "hash",
    "fake",
    "ruri-v3-310m",
    "ruri-v3-130m",
    "bge-m3",
    "qwen3-embedding-0.6b",
]
ChatConsoleReranker = Literal[
    "none",
    "fake",
    "ruri-v3-reranker-310m",
    "qwen3-reranker-0.6b",
]


class APIModel(BaseModel):
    """Base model that rejects unexpected request fields."""

    model_config = ConfigDict(extra="forbid")


class HealthResponse(APIModel):
    ok: bool
    service: str
    version: str
    localhost_only: bool


class QueryRequest(APIModel):
    question: str = Field(min_length=1)
    db_path: Path | None = None
    limit: int = Field(default=8, gt=0, le=50)
    sources: list[SourceKind] = Field(default_factory=list)
    since: str | None = None
    until: str | None = None
    semantic_model: SemanticModel = "none"
    client: LeaderClient = "fake"
    model_key: str = "leader"
    show_private: bool = False


class QueryResponse(APIModel):
    question: str
    answer: dict[str, Any]
    evidence: list[dict[str, Any]]
    redacted: bool


class ChatQueryRequest(APIModel):
    question: str = Field(min_length=1)
    db_path: Path | None = None
    mode: ChatConsoleMode = "retrieval-only"
    sources: list[SourceKind] = Field(default_factory=list)
    leader_plan: bool = True
    leader_rerank: bool = True
    semantic: bool = False
    semantic_model: ChatConsoleSemanticModel = "hash"
    semantic_top_k: int | None = Field(default=20, gt=0, le=200)
    semantic_weight: float = Field(default=1.0, ge=0.0, le=10.0)
    reranker: ChatConsoleReranker = "none"
    rerank_top_k: int | None = Field(default=20, gt=0, le=200)
    retrieval_repair: int = Field(default=1, ge=0, le=3)
    strict_relevance: bool = False
    minimum_relevance_score: float = Field(default=0.6, ge=0.0, le=1.0)
    show_answer: bool = True
    show_snippets: bool = False
    show_photo_thumbnails: bool = True
    show_full_text: bool = False
    show_raw_model_output: bool = False
    snippet_chars: int = Field(default=160, gt=0, le=500)
    limit: int = Field(default=5, gt=0, le=20)
    temporal_top_candidate_dates: int = Field(default=10, gt=0, le=50)
    temporal_top_evidence_per_date: int = Field(default=5, gt=0, le=20)
    verify_with_vision: bool = False
    max_live_vision_checks: int = Field(default=0, ge=0, le=20)
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_tokens: int = Field(default=256, gt=0, le=4096)
    model_key: str = "leader"
    embedding_device: Literal["auto", "cpu", "cuda"] = "auto"


class ChatQueryResponse(APIModel):
    ok: bool
    request_id: str | None = None
    run_id: str | None = None
    mode: ChatConsoleResponseMode
    answer_state: str | None = None
    answer_succeeded: bool | None = None
    evidence_builder_succeeded: bool | None = None
    answer_synthesis_succeeded: bool | None = None
    candidate_date_count: int = 0
    evidence_reference_count: int = 0
    evidence_count: int = 0
    answer_error_class: str | None = None
    answer_error_message: str | None = None
    error_class: str | None = None
    error_message: str | None = None
    failure_stage: str | None = None
    failure_actor: str | None = None
    recovered_failure_count: int = 0
    recovered_failures: list[dict[str, Any]] = Field(default_factory=list)
    current_status: dict[str, Any] | None = None
    answer: dict[str, Any]
    evidence: list[dict[str, Any]]
    evidence_display: dict[str, Any] | None = None
    temporal_event: dict[str, Any] | None = None
    visual_query: dict[str, Any] | None = None
    matching_photos: list[dict[str, Any]] = Field(default_factory=list)
    matching_photo_count: int = 0
    query_type: str | None = None
    date_range: dict[str, Any] | None = None
    event_type: str | None = None
    event_subtype: str | None = None
    diagnostics: dict[str, Any] | None = None
    candidate_dates: list[dict[str, Any]] = Field(default_factory=list)
    trace: dict[str, Any]
    trace_events: list[dict[str, Any]] = Field(default_factory=list)
    trace_summary: dict[str, Any] = Field(default_factory=dict)
    model_usage_summary: dict[str, Any] = Field(default_factory=dict)
    tool_usage_summary: dict[str, Any] = Field(default_factory=dict)
    fallback_summary: dict[str, Any] = Field(default_factory=dict)
    privacy: dict[str, Any]
    warnings: list[str]


class ChatRunStartResponse(APIModel):
    run_id: str
    mode: ChatConsoleResponseMode | None = None
    status: str
    current_step: dict[str, Any] | None = None
    recent_steps: list[dict[str, Any]] = Field(default_factory=list)
    next_step_hint: str | None = None
    elapsed_ms: int = 0
    warnings: list[str] = Field(default_factory=list)
    model_usage_summary: dict[str, Any] = Field(default_factory=dict)
    tool_usage_summary: dict[str, Any] = Field(default_factory=dict)
    fallback_summary: dict[str, Any] = Field(default_factory=dict)
    recovered_failure_count: int = 0
    recovered_failures: list[dict[str, Any]] = Field(default_factory=list)
    result_ready: bool = False
    result_available: bool = False
    result_saved_at: str | None = None
    terminal: bool = False
    completion_summary: dict[str, Any] | None = None
    failure_summary: dict[str, Any] | None = None
    failure_stage: str | None = None
    failure_actor: str | None = None


class ChatRunStatusResponse(ChatRunStartResponse):
    pass


class ChatRunEventsResponse(APIModel):
    run_id: str
    status: str
    result_ready: bool = False
    result_available: bool = False
    result_saved_at: str | None = None
    terminal: bool = False
    trace_events: list[dict[str, Any]] = Field(default_factory=list)
    model_usage_summary: dict[str, Any] = Field(default_factory=dict)
    tool_usage_summary: dict[str, Any] = Field(default_factory=dict)
    fallback_summary: dict[str, Any] = Field(default_factory=dict)


class SystemStatusResponse(APIModel):
    ok: bool
    app_version: str | None = None
    git_commit: str | None = None
    api_response_schema_version: str | None = None
    ui_response_schema_version: str | None = None
    localhost_only: bool
    db_exists: bool
    counts: dict[str, Any]
    indexes: dict[str, Any]
    models: list[dict[str, Any]]
    privacy: dict[str, Any]
    warnings: list[str]


class IngestPhotosRequest(APIModel):
    path: Path
    db_path: Path | None = None
    dry_run: bool = True


class IngestPhotosResponse(APIModel):
    scanned: int
    imported: int
    skipped_duplicates: int
    skipped_unsupported: int
    errors: int
    dry_run: bool


class IngestLineRequest(APIModel):
    path: Path
    db_path: Path | None = None
    dry_run: bool = True


class IngestLineResponse(APIModel):
    files_scanned: int
    messages_parsed: int
    messages_imported: int
    skipped_duplicates: int
    errors: int
    dry_run: bool


class IngestNotesRequest(APIModel):
    path: Path
    db_path: Path | None = None
    dry_run: bool = True


class IngestNotesResponse(APIModel):
    files_scanned: int
    notes_parsed: int
    notes_imported: int
    skipped_duplicates: int
    skipped_unsupported: int
    errors: int
    dry_run: bool


class EventsResponse(APIModel):
    events: list[dict[str, Any]]
    redacted: bool


class EntitiesResponse(APIModel):
    entities: list[dict[str, Any]]
    redacted: bool


class ErrorResponse(APIModel):
    detail: str
