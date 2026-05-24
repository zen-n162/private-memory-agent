"""SQLite storage foundation for Private Memory Agent."""

from private_memory_agent.storage.database import Storage, connect, initialize_database
from private_memory_agent.storage.repositories import (
    AuditLogRepository,
    EmbeddingRepository,
    EntityRepository,
    EventRepository,
    EvidenceLinkRepository,
    LineMessageRepository,
    MediaAnnotationRepository,
    MediaItemRepository,
    NoteRepository,
    SourceItemRepository,
    TextAnnotationRepository,
)

__all__ = [
    "AuditLogRepository",
    "EmbeddingRepository",
    "EntityRepository",
    "EventRepository",
    "EvidenceLinkRepository",
    "LineMessageRepository",
    "MediaAnnotationRepository",
    "MediaItemRepository",
    "NoteRepository",
    "SourceItemRepository",
    "TextAnnotationRepository",
    "Storage",
    "connect",
    "initialize_database",
]
