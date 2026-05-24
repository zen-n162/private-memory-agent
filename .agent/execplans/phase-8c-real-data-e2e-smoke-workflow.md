# ExecPlan: Phase 8-C Real-Data E2E Smoke Workflow

## Goal

Add a privacy-safe `pma e2e smoke` workflow that verifies the local pipeline from existing stored metadata through retrieval, evidence packing, and optional structured answer generation. The command is intended for real local data status checks without ingesting, annotating, modifying source files, or printing private content.

## Non-goals

- Do not ingest photos, LINE exports, or notes.
- Do not run photo annotation or model-heavy batches.
- Do not improve answer quality or retrieval ranking.
- Do not print raw LINE text, note bodies, captions, filenames, full paths, GPS, EXIF, OCR, or personal names.
- Do not require real model endpoints, GPU, network, or private data in tests.

## Current state

The repository already includes:

- SQLite metadata storage and migrations.
- Photo, LINE, and notes ingestion.
- Photo annotations and Phase 8-B privacy-safe stats commands.
- SQLite text indexing and retrieval.
- `RetrievalService`, evidence packing, `LeaderAgent`, `FakeLeaderChatModelClient`, `PrivacyGuard`, and real-compatible OpenAI HTTP runtime clients.
- `pma stats`, `pma retrieve`, `pma query`, and model ping commands.
- `docs/overview_ja.html` maintenance rules that require updates when CLI workflows change.

The gap is a single repeatable E2E smoke command that checks real-data readiness safely and consistently.

## Proposed design

Add an E2E smoke service module that:

- Loads app configuration and a safe smoke-query profile.
- Checks whether the configured SQLite database exists.
- Reports aggregate counts and index availability.
- Runs configured safe queries through `RetrievalService` without printing snippets.
- Uses redacted evidence packing for optional answer generation.
- Defaults to fake local answer generation unless `--dry-run`, `--retrieval-only`, or `--real-model` is selected.
- Supports JSON and human-readable output.

The CLI command will be:

```bash
pma e2e smoke --config configs/paths.local.yaml
```

with modes:

- `--dry-run`
- `--retrieval-only`
- `--fake-model`
- `--real-model`
- `--json`

## Data contracts

Add dataclasses:

- `E2ESmokeQuery`: configured smoke query text and source filters.
- `E2ESmokeCounts`: aggregate database counts.
- `E2EIndexStatus`: text and embedding index status.
- `E2ESmokeQueryResult`: safe per-query status with evidence counts, IDs, and answer status.
- `E2ESmokeReport`: top-level report with no private payloads.

Add query config:

```yaml
queries:
  photos_outing:
    text: "最近の写真説明から、外出に関係しそうな記録を探してください。"
    sources: photos
```

Source lists are comma-separated strings to fit the existing small YAML parser.

## Files to change

- `.agent/execplans/phase-8c-real-data-e2e-smoke-workflow.md`
- `configs/e2e_smoke_queries.example.yaml`
- `src/private_memory_agent/e2e.py`
- `src/private_memory_agent/cli.py`
- `src/private_memory_agent/retrieval/text.py`
- `src/private_memory_agent/retrieval/evidence.py`
- `tests/test_e2e_smoke.py`
- `docs/MODEL_RUNTIME.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add the smoke query example config with generic Japanese queries only.
2. Add E2E smoke report dataclasses and query-profile loading.
3. Add database count and index-status checks.
4. Add retrieval smoke checks that expose only source kinds and evidence IDs.
5. Add fake and real model answer modes using redacted evidence.
6. Add CLI parser and formatter for `pma e2e smoke`.
7. Add synthetic tests for no DB, empty DB, source-specific data, mixed data, modes, JSON output, and privacy-safe output.
8. Update runtime docs, roadmap, and Japanese overview.
9. Run verification commands.

## Tests and verification

Add tests that use temporary SQLite databases only:

- no DB
- empty DB
- photos only
- LINE only
- notes only
- mixed sources
- retrieval-only mode
- fake-model mode
- privacy-safe output
- no raw private paths in output

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

If a local DB exists, run:

```bash
pma stats --config configs/paths.local.yaml
pma e2e smoke --config configs/paths.local.yaml --dry-run
pma e2e smoke --config configs/paths.local.yaml --retrieval-only
```

## Privacy and security

The smoke command reports only aggregate counts, source availability, safe query ordinals, evidence IDs, and answer status. It does not print private filenames, paths, raw text, captions, GPS, EXIF, OCR, or names. Query text is not echoed because local query overrides may contain private words. The command does not modify source files, ingest new records, or annotate photos.

## Performance and hardware

Default mode uses the fake leader model and does not require GPU or a model server. `--real-model` is explicit and uses configured local endpoints only. No Docker or model server is started automatically.

## Rollback

Remove the new CLI command, `src/private_memory_agent/e2e.py`, the query config, tests, and documentation additions. Existing ingestion, retrieval, annotation, and query commands remain untouched.

## Open questions

None blocking.
