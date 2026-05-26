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
- Phase 8-H adds golden question evaluation:
  `configs/golden_questions.example.yaml`, ignored local overrides, and
  `pma eval golden` for retrieval-only, fake-model, and real-model answer
  quality checks with Markdown/JSONL reports and manual rating placeholders.
- Phase 8-I calibrates golden retrieval:
  golden questions can declare expected, required, preferred, and excluded
  sources; `pma eval golden` accepts source constraint flags and reports
  source-policy diagnostics so irrelevant source coverage is visible.
- Phase 8-J calibrates golden evidence relevance:
  golden questions can declare expected, optional, and negative keywords;
  `pma eval golden` can append CLI keywords, boost matching evidence, penalize
  negative hits, and report privacy-safe keyword/relevance diagnostics.
- Phase 8-K adds optional leader-guided retrieval planning:
  the local leader can create a structured retrieval plan, deterministic
  relevance judging can demote generic-only evidence, and weak planned retrieval
  can run one repair loop without exposing raw evidence by default.
- Phase 8-L adds evidence acceptance:
  golden evaluation now distinguishes candidate retrieval from usable evidence,
  reports source, keyword, plan, and final relevance scores separately, and can
  fail strict quality gates when every candidate is generic or weak.
- Phase 8-M adds semantic repair support:
  E2E/golden retrieval can opt into persisted local hash embeddings with
  `--semantic`, reports semantic candidate counts, and expands repair queries
  from specific plan concepts/main entities instead of generic-only terms.
- Phase 8-N adds real semantic model selection:
  `pma index embeddings --model ruri-v3-310m --source line --source notes`
  builds resume-safe local embeddings, E2E/golden can select
  `--semantic-model ruri-v3-310m`, and optional local rerankers can be selected
  with `--reranker`.
- Phase 8-O adds semantic quality comparison:
  `pma eval semantic-compare` compares text-only, hash semantic, real semantic,
  reranker, and leader-planned variants by judged usable evidence rather than
  candidate counts alone, with embedding device diagnostics.
- Phase 9-A adds a localhost evidence-first agent console:
  `/ui` now calls `POST /api/chat/query` and `GET /api/system/status`, showing
  answer status, evidence ids, source coverage, relevance metadata,
  leader-plan counters, retrieval repair status, and privacy state. Answer text
  and snippets stay hidden unless explicitly enabled.
- Phase 9-A2 improves the console chat UX:
  `/ui` now checks `show_answer` by default, while `show_snippets` remains off
  by default. Hidden, unknown, failed, and visible answers are displayed as
  distinct states.
- Phase 9-B adds temporal multimodal event queries:
  outing/date questions such as `2025年12月で出かけたのはいつ？` use deterministic
  date parsing, read-only photo date-range search, outing likelihood scoring,
  daily clustering, and same-day LINE/notes support counts. The UI separates
  used evidence from examined candidates and weak/rejected evidence.
- Phase 9-C0 adds media timestamp audit/backfill:
  `pma media timestamps audit` reports `taken_at` coverage and extractability
  counts without paths, while `pma media timestamps backfill` can dry-run or
  explicitly write capture timestamps with source/confidence provenance.
- Phase 9-C adds temporal coverage diagnostics and fallback search:
  temporal query results now expose parsed date ranges, `taken_at` coverage,
  photo filter-stage counts, nearby month counts, and safe LINE/notes fallback
  counts/evidence IDs when photo candidates are missing or weak.

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
pma eval golden --config configs/paths.local.yaml --retrieval-only --query-limit 2 --json
pma eval golden --config configs/paths.local.yaml --retrieval-only --query-id qst_preparation --require-source line --require-source notes --exclude-source photos --json
pma eval golden --config configs/paths.local.yaml --retrieval-only --query-id qst_preparation --require-source line --require-source notes --exclude-source photos --expected-keyword QST --expected-keyword 面接 --expected-keyword 内定 --json
pma eval golden --config configs/paths.local.yaml --retrieval-only --query-id qst_preparation --leader-plan --json
pma eval golden --config configs/paths.local.yaml --retrieval-only --query-id qst_preparation --leader-plan --leader-rerank --retrieval-repair 1 --json
pma eval golden --config configs/paths.local.yaml --fake-model --query-limit 2 --json
pma eval golden --config configs/paths.local.yaml --real-model --query-limit 1 --timeout-seconds 600 --max-tokens 512 --json
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

## Golden Question Evaluation

Use `configs/golden_questions.example.yaml` as the public template and create
`configs/golden_questions.local.yaml` for private local questions. The local
file is ignored by Git. Keep question ids non-private if you plan to share
reports.

Start with retrieval-only and fake-model checks:

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only --query-limit 2 --json
pma eval golden --config configs/paths.local.yaml --fake-model --query-limit 2 --json
```

Then run one real-model question:

```bash
pma eval golden --config configs/paths.local.yaml --real-model --query-limit 1 --timeout-seconds 600 --max-tokens 512 --json
```

Markdown and JSONL reports can be written under ignored local paths:

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only --query-limit 2 \
  --output data/local/reports/golden_eval.md \
  --output-jsonl data/local/reports/golden_eval.jsonl
```

Default reports hide question text, answer text, snippets, raw evidence, and
raw model output. Use `--show-answer` only for local review. Use
`--show-snippets` only when you explicitly need truncated evidence snippets.
Do not paste private answer reports into public chats.

Golden questions can constrain source coverage:

```yaml
expected_sources: [line, notes]
required_sources: [notes]
preferred_sources: [line]
excluded_sources: [photos]
expected_keywords: [研究]
optional_keywords: [予定, 準備]
negative_keywords: []
evaluation_focus: [evidence_relevance, source_coverage]
```

Use `--source-policy strict` when missing expected/required sources should fail
the evaluation. The default `soft` policy records missing sources and excluded
source violations in diagnostics without failing solely because an expected
source is absent. Excluded sources are filtered from golden retrieval before
evidence is selected.

Source coverage alone does not prove evidence relevance. Add
`expected_keywords`, `optional_keywords`, and `negative_keywords` to local
golden questions, or append one-off keywords with `--expected-keyword` and
`--negative-keyword`. Golden retrieval expands the query with expected/optional
keywords, boosts matching evidence, penalizes negative hits, and reports
`relevance_score`, missing expected keywords, and per-evidence keyword hit
counts without printing raw evidence. Use `--keyword-policy strict` when keyword
misses should fail a retrieval calibration run.

Leader-guided planning is optional because it calls the local leader model and
is slower than deterministic retrieval. Use `--leader-plan` to create a
structured plan, `--leader-rerank` to apply plan-aware deterministic relevance
judging, and `--retrieval-repair 1` to retry weak evidence with additional plan
queries. Default output hides the full plan; use `--show-plan` only locally.

Candidate retrieval is not the same as usable evidence. Use
`--minimum-relevance-score`, `--require-usable-evidence`, and
`--relevance-policy strict` when a golden question should fail if all candidates
are generic-only or weak:

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only \
  --query-id qst_preparation \
  --leader-plan \
  --leader-rerank \
  --retrieval-repair 1 \
  --minimum-relevance-score 0.6 \
  --require-usable-evidence \
  --relevance-policy strict \
  --json
```

To include local semantic retrieval in the same diagnostic path:

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only \
  --query-id qst_preparation \
  --leader-plan \
  --leader-rerank \
  --retrieval-repair 1 \
  --semantic \
  --semantic-model ruri-v3-310m \
  --json
```

The report keeps repair query text hidden by default and shows only safe counts
such as `semantic_candidate_count`, `repair_specific_query_count`, and
`repair_generic_query_count`.

Build real local embeddings only when the model directory exists:

```bash
pma index embeddings --config configs/paths.local.yaml \
  --model ruri-v3-310m \
  --source line \
  --source notes \
  --skip-existing
```

Optional reranking stays explicit:

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only \
  --query-id qst_preparation \
  --semantic \
  --semantic-model ruri-v3-310m \
  --reranker ruri-v3-reranker-310m \
  --rerank-top-k 20 \
  --json
```

Compare retrieval quality across configurations:

```bash
pma eval semantic-compare --config configs/paths.local.yaml \
  --query-id qst_preparation \
  --embedding-device cpu \
  --json
```

Configurations without leader relevance judging are marked
`quality_judged=false`, so reranker-only candidate improvements are not treated
as final answer quality.

## Local Agent Console

Run the localhost API and open the evidence-first console:

```bash
pma api serve --config configs/paths.local.yaml --host 127.0.0.1 --port 8787
```

Then open:

```text
http://127.0.0.1:8787/ui
```

Recommended order:

1. Start with `retrieval-only`.
2. Try `fake-model` for structured answer validation.
3. Use `real-model` only after `pma models ping leader` succeeds.

The UI shows answer text by default because it is a local-only chat console.
Evidence snippets remain hidden by default. Disable `show_answer` when you want
a metadata-only run; enable `show_snippets` only when you need truncated local
evidence snippets. Answer and snippet output may contain private
evidence-derived content and should not be pasted into public chats.

## Temporal Event Queries

For outing/date questions, start with the safe CLI path:

```bash
pma query "2025年12月で出かけたのはいつ？" --config configs/paths.local.yaml
```

This path parses obvious date ranges, searches photo metadata by date, scores
outing likelihood from annotation categories and safe metadata, clusters
candidates by day, and optionally counts same-day LINE/notes support. It returns
candidate dates and evidence IDs, not filenames, full paths, GPS, raw LINE text,
note bodies, OCR text, or full captions.

In the local UI, candidate dates appear in the answer panel. Evidence is grouped
as used, examined candidate, or rejected/weak. Evidence with `should_use=false`
is never marked as `used_by_answer=true`.

Use temporal diagnostics when a result is unknown:

```bash
pma query "2025年12月で出かけたのはいつ？" \
  --config configs/paths.local.yaml \
  --temporal-diagnostics
```

The output includes the parsed date range, query column, media timestamp
coverage, photo candidate counts before and after filters, nearby month counts,
and LINE/notes fallback support counts. This separates "no photos in that
month" from "photo timestamps are missing" and from "photos existed but were
filtered as weak/non-outing candidates."

Temporal event queries require usable `media_items.taken_at` values. Check
coverage before judging temporal quality:

```bash
pma media timestamps audit --config configs/paths.local.yaml
pma media timestamps audit --config configs/paths.local.yaml --month-histogram
pma media timestamps backfill --config configs/paths.local.yaml \
  --dry-run \
  --limit 20 \
  --method auto
```

Backfill is dry-run by default. After backing up the local SQLite DB and
reviewing count-only diagnostics, use `--apply` to update SQLite metadata:

```bash
pma media timestamps backfill --config configs/paths.local.yaml \
  --limit 100 \
  --method auto \
  --only-missing \
  --apply
```

`--write` remains an alias, but `--apply` is the preferred command. Source files
remain read-only. `exiftool` is preferred when available; Pillow is the
lightweight fallback for image EXIF. `--fallback file-mtime` is low-confidence
and should be used only when capture metadata is unavailable and modification
time is acceptable for the use case.
Audit extraction probing is sampled by default for large libraries. Use
`--extract-limit 0` only for an intentional deep all-file audit.
