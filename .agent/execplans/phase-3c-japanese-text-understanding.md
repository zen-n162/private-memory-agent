# ExecPlan: Phase 3-C Japanese Text Understanding Pipeline

## Goal

Add structured extraction for Japanese LINE messages and notes using a pluggable local text understanding client, with strict JSON validation and local SQLite persistence that never overwrites original text.

## Non-goals

- Do not require Qwen3-Swallow or any real model in unit tests.
- Do not trust or store malformed model output as structured extraction.
- Do not implement answer generation, long-context summarization, or agent orchestration.
- Do not log raw LINE messages, note bodies, personal names, or extracted private content.
- Do not mutate source LINE or note rows.

## Current State

The repository already has:

- LINE ingestion into `line_messages`.
- Notes ingestion into `notes`.
- `ChatModelClient` and an OpenAI-compatible local HTTP client for local model servers.
- Fake runtime clients for tests.
- A `pma annotate photos` command and annotation package pattern.

The schema does not yet have a text annotation table, and there is no CLI command for text understanding.

## Proposed Design

Add a runtime text understanding interface:

- `TextUnderstandingClient`
- `TextUnderstandingRequest`
- `TextUnderstandingResponse`
- `FakeTextUnderstandingClient`
- `ChatTextUnderstandingClient`, which wraps `ChatModelClient` and requests strict JSON output.

Add a new SQLite migration for `text_annotations`.

Add `private_memory_agent.annotation.text` with:

- extraction dataclasses
- strict JSON parser and validator
- source selection for `line_messages` and `notes`
- resume-safe `annotate_text`

CLI:

- `pma annotate text --source line|notes`
- `--client fake|openai-compatible`
- `--model-key japanese_text`
- `--limit`
- `--batch-size`

The default real client is the configured OpenAI-compatible local endpoint. Unit tests explicitly use fake clients.

## Data Contracts

Required JSON shape from the model:

```json
{
  "entities": [{"text": "string", "type": "person|place|org|thing|unknown", "confidence": 0.0}],
  "topics": ["string"],
  "dates": [{"text": "string", "normalized": "YYYY-MM-DD or null", "role": "mentioned"}],
  "action_items": [{"text": "string", "due_date": null, "assignee": null, "confidence": 0.0}],
  "event_hints": [{"title": "string", "date_text": null, "confidence": 0.0}],
  "summary": "string",
  "confidence": 0.0
}
```

`text_annotations` columns:

- `source_table`
- `source_id`
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
- privacy fields and timestamps

## Files to Change

- `.agent/execplans/phase-3c-japanese-text-understanding.md`
- `docs/DATA_MODEL.md`
- `docs/MODEL_RUNTIME.md`
- `src/private_memory_agent/runtime/clients.py`
- `src/private_memory_agent/runtime/__init__.py`
- `src/private_memory_agent/storage/migrations.py`
- `src/private_memory_agent/storage/repositories.py`
- `src/private_memory_agent/storage/database.py`
- `src/private_memory_agent/annotation/__init__.py`
- `src/private_memory_agent/annotation/text.py`
- `src/private_memory_agent/cli.py`
- `tests/test_text_understanding.py`
- `tests/test_runtime_clients.py` if runtime export tests need updates

## Implementation Steps

1. Add text understanding runtime schemas, protocol, fake client, and chat-backed adapter.
2. Add migration version 3 for `text_annotations`.
3. Add a repository wrapper for `text_annotations`.
4. Implement strict extraction JSON validation.
5. Implement source selection and resume-safe storage for LINE and notes.
6. Add `pma annotate text --source line|notes`.
7. Add tests using artificial Japanese fixtures and fake clients.
8. Update docs for model runtime and data model.
9. Run `pytest -q`.

## Tests and Verification

Run:

- `pytest -q`

Tests cover:

- Fake client extraction for LINE and notes.
- Strict JSON rejection for malformed output.
- Resume-safe behavior.
- `--limit` and `--batch-size`.
- CLI output does not include raw private text or extracted content.

## Privacy and Security

Original LINE and note text is never overwritten. CLI summaries are count-only. Raw text and model outputs are not logged. Malformed model JSON is rejected and counted as an error rather than being partially trusted.

## Performance and Hardware

Default tests are CPU-only and model-free. Real Qwen3-Swallow or equivalent local models must be started by the user. Batch size controls per-run load; default is conservative.

## Rollback

Remove the runtime additions, annotation text module, CLI command, docs additions, tests, and migration 3 from future fresh databases. Existing local `text_annotations` rows are derived data and can be excluded or deleted manually.

## Open Questions

None blocking.
