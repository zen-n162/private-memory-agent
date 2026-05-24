# Roadmap

This document outlines the development roadmap for private-memory-agent.

## Goals

- build modular memory ingestion
- support secure retrieval
- add evaluation and UI workflows

## Current Phase Notes

- Phase 8-B added privacy-safe annotation batch audit commands:
  `pma stats`, `pma annotate photos --status`, and
  `pma annotate photos --failed`.
- Phase 8-C adds a real-data E2E smoke workflow:
  `pma e2e smoke --config configs/paths.local.yaml`.
- Phase 8-D0 adds schema-aware retrieval diagnostics:
  `pma db schema`, `pma retrieve audit`, and no-fallback E2E smoke mode.
- Phase 8-D improves real evidence recovery:
  photo annotations are included in `pma index text`, Japanese full-sentence
  smoke queries use a keyword LIKE fallback, and `--diagnose` reports retrieval
  stage counts without private payloads.
- Phase 8-D1 adds SQL compatibility surfaces for local aggregate inspection:
  a `text_documents` view and derived `embeddings.source_type`.
- Phase 8-E recovers notes retrieval coverage by searching requested text
  sources separately, adding note-specific diagnostics, and supporting
  `--require-source notes` smoke checks. The fake leader smoke path now stays
  conservative on weak LINE/note evidence and references each used source.
- Phase 8-F adds guarded real-model E2E smoke for the configured DeepSeek-style
  leader endpoint, with `/v1/models` preflight, `--query-limit`, `--query-id`,
  `--timeout-seconds`, `--max-tokens`, compact evidence budgeting, chat smoke,
  OpenAI-compatible JSON response format requests, and robust strict-JSON
  extraction.
- Phase 8-F2 improves DeepSeek/R1 JSON compatibility with explicit answer
  schema prompts, `<think>` stripping, extraction strategy diagnostics,
  one-shot JSON repair retry, `pma models ping --json-smoke`, and photo
  annotation text-index lag warnings.
- Phase 8-F3 tightens real-evidence JSON output control with allowed evidence
  id/source lists, first/last-character JSON instructions, opt-in
  `--response-format-json`, safe output-shape metadata, and explicit
  `--show-model-output` debugging.
- Phase 8-G adds explicit real-model answer display and audit controls:
  default E2E output hides answer text, `--show-answer` displays structured
  local answers without evidence snippets, `--show-snippets` is a separate
  private debugging flag, and `answer_audit` summarizes answer success,
  validation, retry, confidence, evidence-reference, and source coverage.

## 実データE2E smokeの実行手順

Run these checks in order when validating the existing local database:

```bash
pma stats --config configs/paths.local.yaml
pma index text --config configs/paths.local.yaml
pma e2e smoke --config configs/paths.local.yaml --dry-run
pma e2e smoke --config configs/paths.local.yaml --retrieval-only
pma e2e smoke --config configs/paths.local.yaml --retrieval-only --no-fallback
pma e2e smoke --config configs/paths.local.yaml --retrieval-only --diagnose --json
pma e2e smoke --config configs/paths.local.yaml --retrieval-only --no-fallback --require-source notes --json
pma e2e smoke --config configs/paths.local.yaml --fake-model
pma models ping leader --config configs/paths.local.yaml
pma models ping leader --config configs/paths.local.yaml --chat-smoke --max-tokens 64 --timeout-seconds 300
pma models ping leader --config configs/paths.local.yaml --json-smoke --max-tokens 128 --timeout-seconds 300
pma e2e smoke --config configs/paths.local.yaml --real-model --query-limit 1 --timeout-seconds 600 --max-tokens 512 --json
pma e2e smoke --config configs/paths.local.yaml --real-model --query-limit 1 --timeout-seconds 600 --max-tokens 512 --show-answer
pma db schema --config configs/paths.local.yaml
pma retrieve audit --config configs/paths.local.yaml --json
```

The first four commands are lightweight and do not require a real leader model
server. `--fake-model` should pass before `--real-model`. Real-model smoke is
explicit and should be used only when the configured local endpoint is already
running; start with `--query-limit 1`. The smoke output is count/status oriented
and does not print filenames, full paths, raw messages, note bodies, GPS, OCR,
or full captions. It also hides answer text unless `--show-answer` is used.
`--show-snippets` is separate and should be treated as private local output.
Do not paste private answer or snippet output into public chats.
If a reasoning model validates retrieval but exhausts
`--max-tokens 256` before final JSON, retry the same single-query command with
`--max-tokens 512` or `--max-tokens 1024`.

Some reported counts are schema-aware. The physical text index table is
`text_search_documents`, and a read-only compatibility view named
`text_documents` exists for local aggregate SQL checks. Use `--no-fallback` to
confirm whether configured smoke queries return real evidence without inventory
fallback.

After photo annotation batches, rerun `pma index text` so `media_annotations`
are searchable through the same text retrieval path as LINE and notes. Retrieval
audit reports FTS, exact LIKE, keyword LIKE, direct media annotation, and final
evidence counts so fallback does not hide a retrieval failure.

If notes exist but do not appear in retrieved evidence, run the diagnose command
and inspect `source_stage_counts.notes`. It reports note candidate counts and
whether notes were dropped because they had no candidates, were filtered out, or
were ranked out.
