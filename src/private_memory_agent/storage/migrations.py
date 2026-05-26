"""SQLite migrations for local metadata storage."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    """A single ordered SQLite migration."""

    version: int
    name: str
    sql: str


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="create_local_metadata_schema",
        sql="""
CREATE TABLE IF NOT EXISTS source_items (
    id INTEGER PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    external_id TEXT,
    content_sha256 TEXT,
    title TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    is_excluded INTEGER NOT NULL DEFAULT 0,
    excluded_at TEXT,
    excluded_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_type, source_uri)
);

CREATE INDEX IF NOT EXISTS idx_source_items_type ON source_items(source_type);
CREATE INDEX IF NOT EXISTS idx_source_items_sha256 ON source_items(content_sha256);
CREATE INDEX IF NOT EXISTS idx_source_items_excluded ON source_items(is_excluded);

CREATE TABLE IF NOT EXISTS media_items (
    id INTEGER PRIMARY KEY,
    source_item_id INTEGER NOT NULL REFERENCES source_items(id) ON DELETE CASCADE,
    media_type TEXT NOT NULL,
    mime_type TEXT,
    file_path TEXT,
    file_size_bytes INTEGER,
    sha256 TEXT,
    width INTEGER,
    height INTEGER,
    duration_seconds REAL,
    taken_at TEXT,
    modified_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    is_excluded INTEGER NOT NULL DEFAULT 0,
    excluded_at TEXT,
    excluded_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_item_id, file_path)
);

CREATE INDEX IF NOT EXISTS idx_media_items_source ON media_items(source_item_id);
CREATE INDEX IF NOT EXISTS idx_media_items_sha256 ON media_items(sha256);
CREATE INDEX IF NOT EXISTS idx_media_items_taken_at ON media_items(taken_at);
CREATE INDEX IF NOT EXISTS idx_media_items_excluded ON media_items(is_excluded);

CREATE TABLE IF NOT EXISTS media_annotations (
    id INTEGER PRIMARY KEY,
    media_item_id INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
    annotation_type TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    value_text TEXT,
    data_json TEXT NOT NULL DEFAULT '{}',
    confidence REAL,
    model_id TEXT,
    is_excluded INTEGER NOT NULL DEFAULT 0,
    excluded_at TEXT,
    excluded_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_media_annotations_media ON media_annotations(media_item_id);
CREATE INDEX IF NOT EXISTS idx_media_annotations_type ON media_annotations(annotation_type);
CREATE INDEX IF NOT EXISTS idx_media_annotations_excluded ON media_annotations(is_excluded);

CREATE TABLE IF NOT EXISTS line_messages (
    id INTEGER PRIMARY KEY,
    source_item_id INTEGER REFERENCES source_items(id) ON DELETE SET NULL,
    conversation_id TEXT,
    message_id TEXT,
    sender_id TEXT,
    sent_at TEXT,
    message_type TEXT,
    body_text TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    is_excluded INTEGER NOT NULL DEFAULT 0,
    excluded_at TEXT,
    excluded_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_line_messages_source ON line_messages(source_item_id);
CREATE INDEX IF NOT EXISTS idx_line_messages_conversation ON line_messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_line_messages_sent_at ON line_messages(sent_at);
CREATE INDEX IF NOT EXISTS idx_line_messages_excluded ON line_messages(is_excluded);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    source_item_id INTEGER REFERENCES source_items(id) ON DELETE SET NULL,
    note_id TEXT,
    title TEXT,
    body_text TEXT,
    created_at_source TEXT,
    updated_at_source TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    is_excluded INTEGER NOT NULL DEFAULT 0,
    excluded_at TEXT,
    excluded_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_source ON notes(source_item_id);
CREATE INDEX IF NOT EXISTS idx_notes_note_id ON notes(note_id);
CREATE INDEX IF NOT EXISTS idx_notes_updated_source ON notes(updated_at_source);
CREATE INDEX IF NOT EXISTS idx_notes_excluded ON notes(is_excluded);

CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY,
    entity_type TEXT NOT NULL,
    canonical_name TEXT,
    display_name TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    is_excluded INTEGER NOT NULL DEFAULT 0,
    excluded_at TEXT,
    excluded_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_canonical ON entities(canonical_name);
CREATE INDEX IF NOT EXISTS idx_entities_excluded ON entities(is_excluded);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    event_type TEXT NOT NULL,
    title TEXT,
    description TEXT,
    started_at TEXT,
    ended_at TEXT,
    confidence REAL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    is_excluded INTEGER NOT NULL DEFAULT 0,
    excluded_at TEXT,
    excluded_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_started_at ON events(started_at);
CREATE INDEX IF NOT EXISTS idx_events_excluded ON events(is_excluded);

CREATE TABLE IF NOT EXISTS evidence_links (
    id INTEGER PRIMARY KEY,
    target_table TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    evidence_table TEXT NOT NULL,
    evidence_id INTEGER NOT NULL,
    relation_type TEXT NOT NULL DEFAULT 'supports',
    weight REAL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    is_excluded INTEGER NOT NULL DEFAULT 0,
    excluded_at TEXT,
    excluded_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evidence_links_target ON evidence_links(target_table, target_id);
CREATE INDEX IF NOT EXISTS idx_evidence_links_evidence ON evidence_links(evidence_table, evidence_id);
CREATE INDEX IF NOT EXISTS idx_evidence_links_excluded ON evidence_links(is_excluded);

CREATE TABLE IF NOT EXISTS embeddings (
    id INTEGER PRIMARY KEY,
    owner_table TEXT NOT NULL,
    owner_id INTEGER NOT NULL,
    embedding_type TEXT NOT NULL,
    model_id TEXT,
    dimensions INTEGER,
    vector_json TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    is_excluded INTEGER NOT NULL DEFAULT 0,
    excluded_at TEXT,
    excluded_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_embeddings_owner ON embeddings(owner_table, owner_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_type ON embeddings(embedding_type);
CREATE INDEX IF NOT EXISTS idx_embeddings_excluded ON embeddings(is_excluded);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    action TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'system',
    target_table TEXT,
    target_id INTEGER,
    status TEXT NOT NULL DEFAULT 'ok',
    detail_json TEXT NOT NULL DEFAULT '{}',
    is_excluded INTEGER NOT NULL DEFAULT 0,
    excluded_at TEXT,
    excluded_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_log_target ON audit_log(target_table, target_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_excluded ON audit_log(is_excluded);
""",
    ),
    Migration(
        version=2,
        name="create_local_text_search_index",
        sql="""
ALTER TABLE line_messages ADD COLUMN normalized_text TEXT;
ALTER TABLE notes ADD COLUMN normalized_text TEXT;

CREATE TABLE IF NOT EXISTS text_search_documents (
    id INTEGER PRIMARY KEY,
    source_table TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    title TEXT,
    body TEXT,
    normalized_text TEXT NOT NULL,
    snippet_text TEXT,
    is_excluded INTEGER NOT NULL DEFAULT 0,
    indexed_at TEXT NOT NULL,
    UNIQUE(source_table, source_id)
);

CREATE INDEX IF NOT EXISTS idx_text_search_documents_source
ON text_search_documents(source_table, source_id);

CREATE INDEX IF NOT EXISTS idx_text_search_documents_excluded
ON text_search_documents(is_excluded);
""",
    ),
    Migration(
        version=3,
        name="create_text_understanding_annotations",
        sql="""
CREATE TABLE IF NOT EXISTS text_annotations (
    id INTEGER PRIMARY KEY,
    source_table TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    annotation_type TEXT NOT NULL DEFAULT 'understanding',
    model_id TEXT,
    summary TEXT,
    entities_json TEXT NOT NULL DEFAULT '[]',
    topics_json TEXT NOT NULL DEFAULT '[]',
    dates_json TEXT NOT NULL DEFAULT '[]',
    action_items_json TEXT NOT NULL DEFAULT '[]',
    event_hints_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL,
    raw_json TEXT NOT NULL DEFAULT '{}',
    is_excluded INTEGER NOT NULL DEFAULT 0,
    excluded_at TEXT,
    excluded_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_table, source_id, annotation_type, model_id)
);

CREATE INDEX IF NOT EXISTS idx_text_annotations_source
ON text_annotations(source_table, source_id);

CREATE INDEX IF NOT EXISTS idx_text_annotations_type
ON text_annotations(annotation_type);

CREATE INDEX IF NOT EXISTS idx_text_annotations_excluded
ON text_annotations(is_excluded);
""",
    ),
    Migration(
        version=4,
        name="create_sql_diagnostic_compatibility_surfaces",
        sql="""
ALTER TABLE embeddings ADD COLUMN source_type TEXT;

UPDATE embeddings
SET source_type = CASE owner_table
    WHEN 'line_messages' THEN 'line'
    WHEN 'notes' THEN 'notes'
    WHEN 'media_items' THEN 'photos'
    WHEN 'media_annotations' THEN 'photos'
    ELSE owner_table
END
WHERE source_type IS NULL;

CREATE INDEX IF NOT EXISTS idx_embeddings_source_type
ON embeddings(source_type);

CREATE VIEW IF NOT EXISTS text_documents AS
SELECT
    id,
    CASE source_table
        WHEN 'line_messages' THEN 'line'
        WHEN 'notes' THEN 'notes'
        WHEN 'media_items' THEN 'photos'
        WHEN 'media_annotations' THEN 'photos'
        ELSE source_table
    END AS source_type,
    source_table,
    source_id,
    title,
    body,
    normalized_text,
    snippet_text,
    is_excluded,
    indexed_at
FROM text_search_documents;
""",
    ),
    Migration(
        version=5,
        name="add_media_timestamp_provenance",
        sql="""
ALTER TABLE media_items ADD COLUMN taken_at_source TEXT;
ALTER TABLE media_items ADD COLUMN taken_at_confidence TEXT;
ALTER TABLE media_items ADD COLUMN taken_at_timezone TEXT;
ALTER TABLE media_items ADD COLUMN taken_at_timezone_unknown INTEGER NOT NULL DEFAULT 1;
ALTER TABLE media_items ADD COLUMN metadata_updated_at TEXT;

CREATE INDEX IF NOT EXISTS idx_media_items_taken_at_source
ON media_items(taken_at_source);

CREATE INDEX IF NOT EXISTS idx_media_items_taken_at_confidence
ON media_items(taken_at_confidence);
""",
    ),
)
