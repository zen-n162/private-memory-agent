# Architecture

This document describes the architecture of the private-memory-agent project.

## Overview

- Agent orchestration
- Storage and retrieval
- Model runtime
- Privacy and security

## Local Data Flow

Private Memory Agent keeps source data local and read-only. Ingestion phases
store metadata and text in SQLite. Annotation phases add derived local metadata
without overwriting originals. Retrieval phases assemble evidence for local
query flows.

## Timeline Reconstruction

Phase 5-A adds a deterministic timeline event builder:

1. Read local metadata from photos, LINE messages, notes, and validated text
   annotations.
2. Parse timestamps with an explicit IANA timezone.
3. Normalize each row into an event candidate with evidence id, source kind,
   participant candidates, place candidates, topic candidates, and confidence.
4. Group nearby candidates by time window and shared people/place/topic signals.
5. Store each group as a tentative `events` row and link supporting rows through
   `evidence_links`.

Events are hypotheses, not facts. The builder does not confirm attendance, infer
face identity, or assert that a participant candidate is a real-world identity.
CLI event listing redacts private titles and candidates by default.

## Entity Resolution

Phase 5-B adds a deterministic entity resolver for people, places,
organizations, topics, and aliases.

Text-understanding annotations can mention people, places, organizations, and
topics. The resolver stores these in `entities` and links the originating local
row through `evidence_links`. `pma entities resolve` performs this step with
count-only output.

Identity policy:

- Person mentions from extracted text become `person_unknown_*` candidates unless
  a user-confirmed alias already matches a known person.
- The resolver does not merge people merely because names look similar.
- `pma entities alias add` is treated as a user-confirmed action and may merge a
  matching unknown candidate into the selected entity.
- Face-to-name assertions are out of scope for this phase.

Entity names and aliases are private. CLI listing redacts them by default.

## Local API And UI

Phase 6-A adds a thin FastAPI layer under `private_memory_agent.api`. Phase 6-B
adds a minimal evidence-first browser UI served by that same app.

The API reuses the same local services as the CLI for query, ingestion, events,
and entities. It is not a separate data path. The app factory stores default
configuration and database paths in app state so tests can use temporary SQLite
databases.

The API is localhost-only in this phase. `pma api serve` binds to `127.0.0.1` by
default and rejects non-loopback hosts because there is no authentication yet.
Responses are redacted by default.

The `/ui` route returns a small static HTML page with inline CSS and JavaScript.
It calls `POST /api/query`, passes source filters, and renders the structured
answer plus evidence snippets returned by the API. The UI does not read source
files, bypass API redaction, or add a separate frontend runtime.

## Evaluation

Phase 7-A adds a synthetic evaluation harness under
`private_memory_agent.evaluation`.

The harness generates small fake LINE, notes, and photo-annotation records in a
temporary or explicit SQLite database. `pma eval run` executes the existing
retrieval and query flow with the fake leader client, then reports deterministic
proxy metrics for evidence recall, groundedness shape, privacy leaks, and
insufficient-evidence handling.

The smoke script uses the same harness. Default evaluation is CPU-only and does
not read real source paths, load models, use GPU, or access the network.
