# Data Model

Private Memory Agent stores local metadata in SQLite. The database is local-only and is not a vector database. Phase 1-A creates the structured foundation that later ingestion, retrieval, and model-runtime phases can use.

The schema is applied by a small migration runner in `private_memory_agent.storage`. It uses `schema_migrations` to record applied versions.

## Privacy Rules

- Source records are local metadata only.
- Repositories must not log private payloads.
- Real source files remain outside the database and are treated as read-only.
- Normal list/get calls hide rows with `is_excluded = 1`.
- Soft removal uses `is_excluded`, `excluded_at`, and `excluded_reason` instead of destructive deletion.
- Payload fields such as LINE message text, note body text, annotation values, and metadata JSON may contain private data in future phases and must not be printed in normal logs.

## Tables

### `source_items`

Represents one raw source item or external source record.

Important fields:

- `source_type`: source category such as `photo`, `line`, or `note`.
- `source_uri`: local source identifier or path-like URI.
- `external_id`: optional source-system id.
- `content_sha256`: optional content hash.
- `metadata_json`: structured metadata.
- Privacy fields: `is_excluded`, `excluded_at`, `excluded_reason`.
- Timestamps: `created_at`, `updated_at`.

Uniqueness: `(source_type, source_uri)`.

### `media_items`

Stores image/video/audio metadata linked to `source_items`.

Important fields:

- `source_item_id`: source item foreign key.
- `media_type`: image, video, audio, etc.
- `mime_type`
- `file_path`
- `file_size_bytes`
- `sha256`
- `width`, `height`, `duration_seconds`
- `taken_at`, `modified_at`
- `metadata_json`
- Privacy fields and timestamps.

Uniqueness: `(source_item_id, file_path)`.

### `media_annotations`

Stores manual or derived media annotations. Phase 1-A does not create model annotations.

Important fields:

- `media_item_id`
- `annotation_type`
- `source`: manual, imported, model, etc.
- `value_text`
- `data_json`
- `confidence`
- `model_id`
- Privacy fields and timestamps.

### `line_messages`

Stores LINE message metadata and optional local message text for later ingestion phases.

Important fields:

- `source_item_id`
- `conversation_id`
- `message_id`
- `sender_id`
- `sent_at`
- `message_type`
- `body_text`
- `metadata_json`
- Privacy fields and timestamps.

`body_text` can be private and must not be logged.

Phase 1-C supports local text exports only. It assumes common Japanese LINE export text with date headers such as `YYYY/MM/DD(曜)` and tab-separated message rows shaped like `HH:MM<TAB>speaker<TAB>message`. Continuation lines are treated as multiline message text when they follow a parsed text message. Omitted media markers such as stickers, photos, images, and videos are stored with `message_type = omitted`. Unparsed non-message rows are preserved as `system` or `malformed` records rather than discarded.

Encrypted LINE backups, external LINE access, unofficial APIs, and real user fixtures are out of scope.

### `notes`

Stores note metadata and optional local note body text.

Important fields:

- `source_item_id`
- `note_id`
- `title`
- `body_text`
- `created_at_source`
- `updated_at_source`
- `metadata_json`
- Privacy fields and timestamps.

`title` and `body_text` can be private and must not be logged.

Phase 1-D supports Markdown, TXT, JSON, and PDF-placeholder note exports. Markdown and TXT content is preserved fully, with simple YAML-like frontmatter support for fields such as `title`, `created`, and `updated`. JSON exports are parsed from common fields such as `title`, `body`, `text`, `content`, and timestamp fields. PDF files are stored with metadata and a placeholder body; no PDF extraction dependency or model summarization is introduced in this phase.

### `entities`

Stores canonical entities such as people, places, organizations, or topics.

Important fields:

- `entity_type`
- `canonical_name`
- `display_name`
- `metadata_json`
- Privacy fields and timestamps.

Names may be private and must not be printed in normal logs.

Phase 5-B stores entity resolver state in `metadata_json` rather than adding a
new schema table. Important metadata keys:

- `aliases`: local aliases for the entity.
- `alias_norms`: normalized aliases for deterministic matching.
- `user_confirmed`: whether a user explicitly confirmed the alias/entity link.
- `identity_status`: `candidate`, `confirmed`, `merged`, or a future review state.
- `candidate_kind`: for example `person_unknown`.
- `merged_into_entity_id`: set on soft-excluded rows merged by a user action.

Unconfirmed people extracted from text are stored as `person_unknown_*`
candidates. They are linked to evidence but are not merged into named people
unless a user-confirmed alias connects them. Places, organizations, and topics
may be reused by normalized alias because they are not person identity claims.

### `events`

Stores timeline events inferred or imported by later phases.

Important fields:

- `event_type`
- `title`
- `description`
- `started_at`, `ended_at`
- `confidence`
- `metadata_json`
- Privacy fields and timestamps.

Phase 5-A writes only tentative event hypotheses with `event_type = tentative`.
These rows are not confirmations. Their `metadata_json` includes:

- `status`: `tentative`
- `timezone`: the IANA timezone used to interpret naive source timestamps
- `group_key`: stable build key for idempotency
- `evidence_ids`: source ids such as `line_messages:1`
- `participants`: participant candidates from extracted entities or source metadata
- `places`: place candidates from extracted entities, annotations, or coarse GPS buckets
- `topics`: topic candidates from text annotations or media object labels
- `source_counts`: counts by photos, LINE, and notes
- `identity_assertions`: always `false` in Phase 5-A
- `hypothesis`: `true`

Participant and place candidates may contain private names or locations and must
not be printed in normal logs. Events remain hypotheses until a future explicit
confirmation workflow marks them otherwise.

### `evidence_links`

Links evidence rows to target rows without requiring a single polymorphic foreign key.

Important fields:

- `target_table`, `target_id`
- `evidence_table`, `evidence_id`
- `relation_type`
- `weight`
- `metadata_json`
- Privacy fields and timestamps.

Phase 5-A links tentative event rows to their supporting `media_items`,
`line_messages`, and `notes` rows with `relation_type = supports`.

Phase 5-B links entities to supporting evidence rows with
`target_table = entities` and `relation_type = mentions`. These links are
evidence of a local mention, not proof of a person's real-world identity.

### `embeddings`

Stores embedding metadata and optional serialized vector JSON. This is not a vector database and does not provide ANN search.

Important fields:

- `owner_table`, `owner_id`
- `source_type`: derived diagnostic source label such as `line`, `notes`, or
  `photos`
- `embedding_type`
- `model_id`
- `dimensions`
- `vector_json`
- `metadata_json`
- Privacy fields and timestamps.

### Compatibility views

`text_documents` is a read-only compatibility view over the canonical
`text_search_documents` table. It adds a derived `source_type` column so local
aggregate diagnostics can use simple SQL without knowing internal table names.
Canonical retrieval code still uses `text_search_documents`.

### `text_annotations`

Stores validated model-derived structure for LINE messages and notes without changing original text rows.

Important fields:

- `source_table`, `source_id`
- `annotation_type`
- `model_id`
- `summary`
- `entities_json`
- `topics_json`
- `dates_json`
- `action_items_json`
- `event_hints_json`
- `confidence`
- `raw_json`
- Privacy fields and timestamps.

Phase 3-C uses this table for Japanese text understanding. Model output must validate against the strict extraction schema before insertion. The original `line_messages.body_text` and `notes.body_text` fields are not overwritten.

The JSON fields may contain private extracted names, topics, dates, and action items. They must not be printed in normal logs.

### `audit_log`

Stores privacy-safe audit events.

Important fields:

- `action`
- `actor`
- `target_table`, `target_id`
- `status`
- `detail_json`
- Privacy fields and timestamps.

`detail_json` must not include raw private payloads.
