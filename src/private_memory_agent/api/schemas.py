"""Pydantic schemas for the local FastAPI API."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SourceKind = Literal["photos", "line", "notes"]
SemanticModel = Literal["none", "hash", "fake"]
LeaderClient = Literal["fake", "openai-compatible"]
EntityType = Literal["person", "place", "organization", "topic"]


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
