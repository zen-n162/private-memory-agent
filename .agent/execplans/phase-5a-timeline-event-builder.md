# ExecPlan: Phase 5-A Timeline Event Builder

## Goal

Build tentative local timeline events from imported photos, LINE messages, notes,
and text-understanding annotations. Add `pma events build` to create event
hypotheses and `pma events list` to inspect them safely.

## Non-goals

- Do not confirm events automatically.
- Do not assert face identity or person identity automatically.
- Do not call LLMs, vision models, embedding models, network services, or APIs.
- Do not ingest new real data.
- Do not print private names, note text, LINE text, file paths, GPS coordinates,
  or raw metadata in normal CLI output.

## Current state

The SQLite schema already includes:

- `events`: timeline event rows with `event_type`, `title`, `description`,
  `started_at`, `ended_at`, `confidence`, `metadata_json`, privacy fields, and
  timestamps.
- `evidence_links`: links target rows such as events to source evidence rows.
- `media_items`: photo/media timestamps and metadata.
- `line_messages`: LINE timestamps and message metadata.
- `notes`: note source timestamps.
- `text_annotations`: validated extracted entities, topics, dates, event hints,
  and confidence for LINE messages and notes.

There is no event-builder service, no event-specific repository helpers, and no
CLI for events.

## Proposed design

Add `private_memory_agent.timeline.events` with:

- `EventBuilder`: reads eligible local metadata rows and text annotations,
  groups them into tentative events by time window and shared candidate signals,
  then writes `events` and `evidence_links`.
- `EventCandidate`: internal normalized evidence item with source table/id,
  timestamp, topics, participant candidates, place candidates, confidence, and
  source kind.
- `TentativeEvent`: in-memory event hypothesis before persistence.
- `EventBuildResult`: count-only CLI-safe result.
- `list_events`: returns privacy-safe event summaries for CLI display.

Grouping is deterministic:

- Parse timestamps with an explicit timezone using `zoneinfo`.
- Naive timestamps are interpreted in the configured/default timezone.
- Nearby candidates within a configurable time window can group together.
- Shared participant, place, or topic candidates increase confidence.
- Event rows use `event_type = 'tentative'`.
- Metadata records `status = 'tentative'`, `timezone`, evidence ids,
  participant/place/topic candidates, source counts, and a stable `group_key`.

Repeated builds skip events with the same `group_key`.

## Data contracts

`EventBuildResult`:

- `events_created: int`
- `events_existing: int`
- `evidence_candidates: int`
- `evidence_links_created: int`
- `timezone: str`

`TentativeEvent`:

- `title: str`
- `start_at: str`
- `end_at: str`
- `evidence_ids: tuple[str, ...]`
- `participants: tuple[str, ...]`
- `places: tuple[str, ...]`
- `topics: tuple[str, ...]`
- `confidence: float`
- `metadata: dict`

`events.metadata_json`:

- `status: "tentative"`
- `timezone`
- `group_key`
- `evidence_ids`
- `participants`
- `places`
- `topics`
- `source_counts`
- `identity_assertions: false`

CLI list output is redacted by default. With `--show-private`, private event
title and candidate names are shown only when config also enables
`log_private_data`.

## Files to change

- `.agent/execplans/phase-5a-timeline-event-builder.md`
- `src/private_memory_agent/timeline/__init__.py`
- `src/private_memory_agent/timeline/events.py`
- `src/private_memory_agent/storage/repositories.py`
- `src/private_memory_agent/storage/__init__.py` if exports are needed
- `src/private_memory_agent/cli.py`
- `tests/test_event_builder.py`
- `docs/DATA_MODEL.md`
- `docs/ARCHITECTURE.md`

## Implementation steps

1. Add event repository helpers for inserting timeline events and linking
   evidence.
2. Implement timestamp parsing with explicit timezone handling.
3. Implement candidate collection from media, LINE, notes, and text annotations.
4. Implement deterministic grouping and confidence scoring.
5. Persist tentative events and evidence links idempotently.
6. Add `pma events build` and `pma events list`.
7. Add synthetic multi-source tests for one day.
8. Update docs and run verification.

## Tests and verification

- `pytest -q`
- `pma events build --help`
- `pma events list --help`

Tests use temporary SQLite databases and synthetic rows only. No GPU, model
files, Docker, network, or private data is required.

## Privacy and security

Events are stored as hypotheses with `status = tentative`. Participant and place
values are candidates, not identity assertions. The builder does not read source
files and does not log raw LINE messages, note bodies, filenames, precise GPS,
or personal names. CLI list output redacts private fields by default.

## Performance and hardware

The builder uses SQL scans and deterministic Python grouping over local metadata.
No GPU or VRAM is used. The default target NVIDIA RTX 4500 Ada 24GB is
irrelevant for this phase.

## Rollback

Remove the timeline package, CLI event commands, tests, and doc updates. Existing
database schemas can remain unchanged. Any generated tentative event rows can be
soft-excluded or deleted manually from local test databases.

## Open questions

None blocking. Later phases can add user confirmation workflows and richer
geospatial clustering.
