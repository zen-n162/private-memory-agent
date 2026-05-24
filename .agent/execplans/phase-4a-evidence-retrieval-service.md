# ExecPlan: Phase 4-A Evidence Retrieval Service

## Goal

Create an evidence retrieval service that gathers and ranks local evidence across photos, LINE messages, and notes, then packs the selected evidence for future LLM prompts without calling any LLM.

## Non-goals

- Do not call the leader/reasoning model.
- Do not generate final answers.
- Do not require real embedding models, Qdrant, GPU, network, or private data in unit tests.
- Do not expose filenames, paths, raw LINE text, note bodies, OCR text, GPS, or personal names in CLI output when privacy redaction is enabled.
- Do not mutate source rows except rebuilding local text indexes when requested by retrieval.

## Current State

The repository already has:

- SQLite storage for photos, LINE, notes, media annotations, text annotations, and embeddings.
- FTS/LIKE text search over LINE messages and notes.
- Optional semantic search over persisted embeddings.
- Photo annotation storage in `media_annotations`.
- Config flags including `log_private_data`.

There is no unified retrieval service that combines these signals into evidence records.

## Proposed Design

Add `private_memory_agent.retrieval.evidence` with:

- `Evidence`: normalized retrieval result with source ids, confidence, score, source kind, signals, and snippet.
- `RetrievalFilters`: date and source filters.
- `RetrievalService`: orchestrates text search, optional semantic search, media annotation search, date/source filtering, ranking, and deduplication.
- `pack_evidence_for_prompt`: deterministic evidence block for future LLM prompts.

CLI:

- Add `pma retrieve "question"`.
- Options: `--db`, `--limit`, `--source`, `--since`, `--until`, `--semantic-model hash|fake|none`, `--config-dir`, `--config`, and `--show-private`.
- Display applies redaction unless config explicitly allows private logging and `--show-private` is used.

## Data Contracts

`Evidence` fields:

- `evidence_id`
- `source_kind`: `photos`, `line`, or `notes`
- `source_table`
- `source_id`
- `title`
- `snippet`
- `occurred_at`
- `confidence`
- `score`
- `signals`
- `metadata`

`RetrievalFilters` fields:

- `sources`
- `since`
- `until`

Evidence packing:

- Numbered blocks.
- Includes evidence id, source kind/table/id, confidence, date, and redacted or full snippet.
- No model calls.

## Files to Change

- `.agent/execplans/phase-4a-evidence-retrieval-service.md`
- `docs/RETRIEVAL.md`
- `src/private_memory_agent/cli.py`
- `src/private_memory_agent/retrieval/__init__.py`
- `src/private_memory_agent/retrieval/evidence.py`
- `tests/test_evidence_retrieval.py`

## Implementation Steps

1. Add evidence dataclasses and redaction helpers.
2. Implement text search collection with date/source metadata enrichment.
3. Implement optional semantic collection using existing embedding helpers.
4. Implement media annotation collection from `media_annotations` and `media_items`.
5. Merge/deduplicate evidence by source table/id and aggregate signals.
6. Rank deterministically by score, confidence, and source id.
7. Add evidence packing for future LLM prompts.
8. Add `pma retrieve "question"` with privacy-safe JSON output.
9. Add fake DB tests for LINE, notes, photos, dedup, filters, semantic optional path, and redaction.
10. Run `pytest -q`.

## Tests and Verification

Run:

- `pytest -q`

Tests should verify:

- FTS/LIKE text evidence from LINE and notes.
- Photo evidence from media annotations.
- Source and date filters.
- Ranking and deduplication across FTS and semantic signals.
- Prompt packing.
- CLI output redacts private snippets by default.

## Privacy and Security

The retrieval service can carry local evidence text internally for future local-only prompt construction, but CLI display redacts title/snippet unless explicitly allowed by config and `--show-private`. Source paths and filenames are never included in evidence output.

## Performance and Hardware

Default retrieval uses SQLite only. Optional semantic retrieval uses persisted embeddings with fake/hash models in tests. No GPU or model server is required.

## Rollback

Remove the evidence retrieval module, CLI command, tests, and docs additions. Existing storage and annotations remain unchanged.

## Open Questions

None blocking.
