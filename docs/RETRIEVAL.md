# Retrieval

Private Memory Agent retrieval is local-first and evidence-oriented.

## Text Search

Phase 2-A adds text search only. It does not use LLMs, embeddings, vector search, rerankers, or external services.

Pipeline:

1. Ingestion stores LINE messages in `line_messages` and notes in `notes`.
2. `pma index text` rebuilds `text_search_documents` from non-excluded LINE messages, notes, and local photo annotations.
3. The indexer normalizes text with Unicode NFKC normalization, case folding, and whitespace collapsing.
4. If SQLite FTS5 is available, the indexer also builds `text_search_fts`.
5. `pma search text "query"` searches FTS5 when available and always uses the normalized table as a deterministic fallback.

The fallback matters for Japanese text because SQLite's default FTS tokenizer is
not a Japanese morphological tokenizer. Phase 8-D adds a deterministic keyword
LIKE fallback for full Japanese questions, while keeping FTS5 in place.

Indexed fields:

- `line_messages.body_text`
- `line_messages.sender_id` as a lightweight title field
- `line_messages.normalized_text`
- `notes.title`
- `notes.body_text`
- `notes.normalized_text`
- `media_annotations.value_text` and safe searchable fields from `data_json`,
  indexed as `source_table='media_items'`

## Embeddings

Phase 2-B and 2-C add embedding interfaces and optional vector stores:

- `EmbeddingModel`: protocol for model adapters.
- `VectorStore`: protocol for vector stores.
- `FakeEmbeddingModel`: deterministic token-count model for tests.
- `HashEmbeddingModel`: deterministic hash-bucket fallback for development.
- `InMemoryVectorStore`: process-local vector store for tests.
- `SentenceTransformersEmbeddingModel`: optional local adapter loaded only when explicitly selected.
- `QdrantVectorStore`: optional Qdrant adapter loaded only when explicitly selected.
- `EvidenceReranker`: optional local reranker interface.

Embedding vectors are persisted in the local `embeddings` table as JSON. This is not a production vector database and does not require Qdrant, FAISS, GPU, or network access by default.

Build local hash embeddings:

```bash
pma index embeddings
pma search semantic "ローカル"
```

Use a real local sentence-transformers-compatible model:

```bash
pma index embeddings --model ruri-v3-310m --source line --source notes --skip-existing
pma search semantic "ローカル" --model ruri-v3-310m
```

Supported public aliases resolve through `configs/models.example.yaml` and the
local model root: `ruri-v3-310m`, `ruri-v3-130m`, `bge-m3`, and
`qwen3-embedding-0.6b`. Hash and fake embeddings remain for tests/dev only.
Real indexing is explicit, resume-safe with `--skip-existing`, and prints
counts only.

Use Qdrant only when the service is already running:

```bash
pma index embeddings --vector-store qdrant --qdrant-url http://localhost:6333
pma search semantic "ローカル" --vector-store qdrant --qdrant-url http://localhost:6333
```

The Qdrant adapter stores vectors with source identifiers only. Result snippets are recovered from the local SQLite database.

## Results

Search commands return structured JSON:

- `source_table`
- `source_id`
- `title`
- `snippet`
- `score`

Snippets are clipped and whitespace-normalized. They are intended for explicit user search results only and should not be written to normal logs.

`pma search text` accepts the same local config overlay shape as other runtime
commands, so local operators can keep one command style:

```bash
pma search text "研究" --config configs/paths.local.yaml
```

## Evidence Retrieval

Phase 4-A adds `pma retrieve "question"` and `RetrievalService`.

The service combines local signals:

- FTS/LIKE text search over LINE messages and notes
- optional persisted semantic embeddings
- photo `media_annotations`
- source filters for `photos`, `line`, and `notes`
- date filters with `--since` and `--until`

Evidence records include:

- source kind
- source table and id
- confidence
- ranking score
- retrieval signals
- date when available
- snippet for future local prompt packing

The command does not call a leader LLM. It only returns and packs evidence for later answer-generation phases.

When a query asks for multiple text sources, PMA searches each requested source
table separately before merging results. This prevents early LINE rows from
filling the candidate window before note rows can be considered. Public source
names are normalized to:

- `photos`
- `line`
- `notes`

Schema-aware audit is available when checking whether retrieval is actually
working:

```bash
pma db schema --config configs/paths.local.yaml
pma retrieve audit --config configs/paths.local.yaml --json
pma e2e smoke --config configs/paths.local.yaml --retrieval-only --no-fallback --json
pma e2e smoke --config configs/paths.local.yaml --retrieval-only --no-fallback --require-source notes --json
```

Important naming detail: PMA's physical text index table is
`text_search_documents`. PMA also provides a read-only compatibility view named
`text_documents` for manual aggregate SQL checks. Diagnostics label whether
counts come from a physical table or compatibility surface.

Photo annotations are retrieved directly from `media_annotations` joined to
`media_items`, and after `pma index text` they are also searchable through
`text_search_documents` as `source_table='media_items'`. Check
`media_annotations_searchable_via` in `pma retrieve audit --json`.

Note coverage can be diagnosed without showing note titles or bodies. Retrieval
audit and `pma e2e smoke --diagnose --json` include `source_stage_counts.notes`
with:

- note FTS candidate count
- note exact LIKE candidate count
- note keyword LIKE fallback candidate count
- note candidates after source filtering
- note evidence retained after ranking
- drop reason, for example `no_text_candidates`, `ranked_out`, or
  `source_not_requested`

Embedding diagnostics can use `embeddings.source_type`, which is derived from
`owner_table` during migration and future inserts. The canonical owner mapping
remains `owner_table` plus `owner_id`.

Manual aggregate SQL examples:

```sql
SELECT source_type, COUNT(*) FROM text_documents GROUP BY source_type;
SELECT source_type, COUNT(*) FROM embeddings GROUP BY source_type;
SELECT COUNT(*)
FROM text_documents
WHERE source_type LIKE '%photo%' OR source_type LIKE '%media%';
```

These queries should be used only for local aggregate inspection. They can
touch indexed text columns if expanded beyond `COUNT(*)`, so prefer PMA's
privacy-safe CLI diagnostics for routine output.

Display privacy:

- CLI output is redacted by default.
- Use of `--show-private` only shows snippets when config also enables private logging.
- Source paths and filenames are not included.
- Packed evidence is redacted whenever display output is redacted.

## Minimal Query Flow

Phase 4-B adds `pma query "question"`.

Flow:

1. `RetrievalService` retrieves local evidence.
2. `LeaderAgent` receives the question and packed evidence.
3. Retrieved evidence is marked as untrusted data, not instructions.
4. The leader client must return strict JSON.
5. `EvidenceCritic` validates references, weak-evidence uncertainty, and obvious source-injection failures.
6. `PrivacyGuard` marks sensitive evidence and redacts display output.
7. The CLI prints a structured answer.

If no evidence is retrieved, the leader model is not called. The answer says there is insufficient local evidence.

Default real-compatible usage expects a user-started local OpenAI-compatible leader endpoint:

```bash
pma query "週末の予定は？" --model-key leader
```

Unit and smoke usage can use the fake local client:

```bash
pma query "週末の予定は？" --client fake
```

The fake leader is deliberately conservative. When retrieved LINE or note
evidence is keyword-only or otherwise weak, it returns lower confidence so the
same `EvidenceCritic` rules used for real clients still pass or fail honestly.
For mixed-source evidence, it references at least one evidence id per used
source so source coverage checks can validate `photos`, `line`, and `notes`
without a real model server.

Real-model E2E smoke uses the same leader answer schema but calls the configured
local OpenAI-compatible endpoint:

```bash
pma models ping leader --config configs/paths.local.yaml
pma models ping leader --config configs/paths.local.yaml --chat-smoke --max-tokens 64 --timeout-seconds 300
pma models ping leader --config configs/paths.local.yaml --json-smoke --max-tokens 128 --timeout-seconds 300
pma e2e smoke --config configs/paths.local.yaml --real-model --query-limit 1 --timeout-seconds 600 --max-tokens 512 --json
```

The real-model path preflights `/v1/models`, resolves the served model name, and
can optionally request `response_format={"type":"json_object"}` with
`--response-format-json` when the endpoint supports OpenAI-compatible JSON
mode. It accepts plain JSON, fenced JSON, `<think>...</think>` blocks followed
by JSON, or text surrounding the final JSON object. The extracted object still
must validate exactly: evidence ids must match retrieved safe ids,
`used_sources` must be backed by those references, and weak evidence must keep
confidence low with explicit unknowns. E2E output reports only safe JSON
diagnostics such as response length, extraction strategy, retry status,
JSON-like brace detection, `<think>` detection, and allowed evidence/source
counts. It does not print raw model output unless `--show-model-output` is
explicitly used.

For answer-quality review, keep the default JSON/status output first. It hides
the conclusion and unknown text, then reports an `answer_audit` summary with
answer success counts, validation errors, retry counts, average confidence,
evidence-reference coverage, and source coverage in the structured answer.
When you are ready to inspect one local answer, add `--show-answer`:

```bash
pma e2e smoke --config configs/paths.local.yaml --real-model --query-limit 1 --timeout-seconds 600 --max-tokens 512 --show-answer
```

`--show-answer` displays the structured answer but not evidence snippets.
`--show-snippets` is a separate local-only debugging flag that prints truncated
snippets and should be treated as private output.

Golden question evaluation is the repeatable answer-quality layer above this
retrieval path. Put safe public examples in `configs/golden_questions.example.yaml`
and private local questions in `configs/golden_questions.local.yaml`:

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only --query-limit 2 --json
pma eval golden --config configs/paths.local.yaml --fake-model --query-limit 2 --json
pma eval golden --config configs/paths.local.yaml --real-model --query-limit 1 --timeout-seconds 600 --max-tokens 512 --json
```

The golden report records retrieval success, answer success, evidence counts,
source counts, used sources, evidence-reference counts, confidence, unknown
counts, retry status, validation errors, and manual rating placeholders. It
hides question text, answers, and snippets by default; use `--show-answer` or
`--show-snippets` only for local inspection.

Golden questions evaluate quality, not just execution. Use source constraints
to make expected coverage explicit:

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

For one-off checks, the CLI can apply constraints without editing the local
question file:

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only \
  --query-id qst_preparation \
  --require-source line \
  --require-source notes \
  --exclude-source photos \
  --expected-keyword QST \
  --expected-keyword 面接 \
  --expected-keyword 内定 \
  --json
```

The golden report includes requested, expected, required, preferred, and
excluded sources, actual evidence source counts, missing expected/required
sources, excluded source violations, `source_policy`, and
`retrieval_passed_source_policy`. `--source-policy strict` fails when expected
or required source evidence is missing. The default `soft` policy records the
mismatch as diagnostics. `--show-snippets --snippet-chars N` is available for
local relevance inspection only; snippets are truncated and still must be
treated as private.

Source constraints solve source coverage, not relevance. Phase 8-J adds golden
keyword calibration for this second step. `expected_keywords` and repeated
`--expected-keyword KEY` values are appended to the golden retrieval query and
boost matching evidence. `optional_keywords` also help retrieval and ranking
but are not treated as required hits. `negative_keywords` and repeated
`--negative-keyword KEY` values penalize evidence that looks off-topic. The
report records `expected_keywords_hit_count`,
`expected_keyword_hit_evidence_count`, `missing_expected_keywords`,
`negative_keyword_hit_count`, per-evidence keyword hit counts, and a simple
`relevance_score` from 0.0 to 1.0. Use `--keyword-policy strict` when missing
expected keywords or negative keyword hits should fail a golden retrieval check.

Keyword diagnostics intentionally do not print evidence text. If relevance is
still poor, inspect locally with `--show-snippets --snippet-chars N`; matched
keywords are listed beside truncated snippets. Treat snippet reports as private
local output and do not paste them into public chats.

Phase 8-K adds optional leader-guided retrieval planning above these
deterministic guardrails. The planner asks the configured local leader model to
turn a question into a structured `RetrievalPlan` with intent, main entities,
specific concepts, generic concepts, temporal hints, source preferences,
retrieval queries, excluded concepts, acceptance criteria, and uncertainty
notes.

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only \
  --query-id qst_preparation \
  --leader-plan \
  --json
```

By default the output does not include raw question text or the full plan. It
only reports safe counters such as `plan_created`,
`retrieval_query_count`, `main_entity_count`, `specific_concept_count`,
`generic_concept_count`, source preferences/constraints, and acceptance-criteria
count. Use `--show-plan` only locally because plan contents may contain private
question-derived terms.

Plan-aware relevance judging is separate and opt-in:

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only \
  --query-id qst_preparation \
  --leader-plan \
  --leader-rerank \
  --retrieval-repair 1 \
  --show-relevance \
  --json
```

The deterministic relevance judge can demote generic-only evidence and promote
evidence matching specific plan concepts. `--retrieval-repair N` allows a weak
planned retrieval to retry with additional plan queries and then merge the
result through the same privacy-safe report path. Source constraints and keyword
diagnostics still apply: they are deterministic guardrails, while leader
planning is an optional intelligent query-understanding layer that is slower and
requires a local leader endpoint.

Phase 8-L separates candidate retrieval from usable evidence acceptance. A
query can now have `candidate_retrieval_succeeded=true` while
`usable_evidence_succeeded=false` if every candidate is judged generic, weak, or
unrelated. Reports include `usable_evidence_count`,
`should_use_evidence_count`, `source_coverage_score`,
`keyword_relevance_score`, `plan_relevance_score`, `final_relevance_score`, and
`relevance_policy_passed`. This prevents generic-only evidence from receiving a
high final relevance score.

Use soft policy to keep diagnostics non-blocking, or strict policy when weak
evidence should fail a quality gate:

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

Repair diagnostics report whether repair was attempted, whether usable evidence
improved, the pre/post usable evidence counts, and the count of repair queries
created. Raw repair query text stays hidden unless the existing `--show-plan`
diagnostic is explicitly requested.

Phase 8-M adds optional local semantic retrieval to the E2E/golden paths. Phase
8-N lets that path use configured real local embedding aliases. It
runs alongside FTS/LIKE retrieval, merges and deduplicates candidates, respects
source filters, and reports `semantic_candidate_count`. The default
`--semantic` mode uses deterministic hash embeddings already persisted by
`pma index embeddings --model-backend hash`; use `--semantic-model fake` only
for tests or matching fake indexes. For real semantic retrieval, build matching
embeddings first and select the same alias:

```bash
pma index embeddings --config configs/paths.local.yaml \
  --model ruri-v3-310m \
  --source line \
  --source notes \
  --skip-existing
```

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only \
  --query-id qst_preparation \
  --leader-plan \
  --leader-rerank \
  --retrieval-repair 1 \
  --semantic \
  --semantic-model ruri-v3-310m \
  --semantic-top-k 20 \
  --semantic-weight 1.0 \
  --json
```

Optional local reranking can be layered on top of merged candidates:

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only \
  --query-id qst_preparation \
  --semantic \
  --semantic-model ruri-v3-310m \
  --reranker ruri-v3-reranker-310m \
  --rerank-top-k 20 \
  --json
```

Rerankers are local-only and are not loaded in unit tests. Default output shows
`semantic_embedding_model_id`, `reranker_model_id`, and safe counts only.

## Semantic Retrieval Quality Comparison

Phase 8-O adds a comparison command for checking which retrieval configuration
actually improves usable evidence:

```bash
pma eval semantic-compare --config configs/paths.local.yaml \
  --query-id qst_preparation \
  --json
```

The comparison runs privacy-safe golden retrieval variants such as:

- `text_only`
- `hash_semantic`
- `ruri_v3_310m`
- `ruri_v3_310m_plus_reranker`
- `leader_plan_ruri`
- `leader_plan_ruri_plus_reranker`

Candidate counts alone are not treated as answer quality. Configurations that
retrieve candidates without leader-plan relevance judging are marked with
`quality_judged=false` and warn that relevance judging did not run. The
recommended configuration is selected from judged configurations using strict
pass, usable evidence count, final relevance score, and source coverage.

Use CPU explicitly if local PyTorch/SentenceTransformers emits CUDA driver
warnings:

```bash
pma eval semantic-compare --config configs/paths.local.yaml \
  --query-id qst_preparation \
  --embedding-device cpu \
  --json
```

The report includes `embedding_device_status` with CUDA availability, warning
detection when available, selected device, and a recommendation. It still hides
question text, evidence snippets, raw plans, filenames, paths, GPS, OCR, LINE
text, note bodies, captions, and raw model output by default.

Repair query expansion now prefers `specific_concepts` and `main_entities` from
the `RetrievalPlan` and avoids generic-only repair terms when specific terms are
available. Reports include `repair_specific_query_count`,
`repair_generic_query_count`, `repair_used_specific_concepts`, and
`repair_used_main_entities`. Full repair query text remains hidden by default
because it can contain private local concepts.

Real-model smoke sends a compact redacted evidence packet by default. Use
`--max-evidence-items` and `--max-evidence-chars` to keep the request small
while checking endpoint, JSON, evidence-id, and critic behavior. Invalid JSON is
retried once by default with a short repair prompt that does not resend full
evidence; tune this with `--json-retry`.
For reasoning-heavy leader models, `--max-tokens 256` is the lightweight first
check; if the endpoint returns sanitized `AnswerValidationError` after
successful retrieval, retry the same `--query-limit 1` command with
`--max-tokens 512` or `--max-tokens 1024`.

If photo annotation batches finish after the last text indexing run, run
`pma index text`. Retrieval/E2E diagnostics warn when
`media_annotations_in_text_index_count` is lower than `media_annotations_count`.

`pma query` does not implement autonomous planning or external calls.

## Evidence Critic And Privacy Guard

Phase 4-C keeps guardrails deterministic:

- Evidence ids in answers must match retrieved evidence ids.
- `used_sources` must be backed by referenced evidence.
- Non-empty answer claims must have at least one known evidence reference.
- Weak evidence requires explicit uncertainty and low confidence.
- If retrieved notes or LINE text contain prompt-injection phrases such as
  `ignore previous instructions`, the final answer must not repeat those
  instructions.

`PrivacyGuard` redacts answer text, questions, titles, and snippets by default
for CLI display. It can also redact likely third-party names, reduce GPS
coordinate precision in metadata, mark sensitive evidence with privacy flags,
and fail closed when a log message would contain known raw private fragments.

This phase does not add an LLM critic. Future phases can add model-assisted
claim decomposition, but unit tests remain deterministic and model-free.

## Evidence-First Agent Console

Phase 9-A adds a local browser console that surfaces retrieval metadata without
making CLI reports larger:

```bash
pma api serve --config configs/paths.local.yaml --host 127.0.0.1 --port 8787
```

Open `http://127.0.0.1:8787/ui`.

The console calls `POST /api/chat/query`, which reuses the E2E retrieval path.
It can show evidence ids, source labels, source coverage, plan metadata counts,
semantic candidate counts, reranked candidate counts, retrieval repair status,
usable evidence status, and per-evidence relevance metadata when plan-aware
judging is enabled.

Default mode is retrieval-only. In fake-model or real-model mode, the local UI
shows answer text by default so the console behaves like a chat interface.
`show_answer` can be turned off for metadata-only checks. `show_snippets`
remains off by default; snippets are truncated/redacted and remain local-only
debugging output.

## Temporal Event Queries

Phase 9-B adds a structured temporal event path for questions such as:

```text
2025年12月で出かけたのはいつ？
去年の夏に外出した日は？
写真から外出した日を教えて
```

When the question contains an obvious date range and outing/event intent, PMA
uses a read-only metadata workflow before falling back to broad text/vector
retrieval:

1. Parse the date range deterministically for forms such as `2025年12月`,
   `2025/12`, `2025-12`, `去年12月`, `先月`, and `去年の夏`.
2. Query `media_items` by capture timestamp in `taken_at` in that range.
3. Score outing likelihood from safe metadata and local photo annotation text.
4. Group candidates by day and add same-day LINE/notes support counts.
5. Return candidate dates with confidence and privacy-safe evidence IDs.

The output separates evidence roles:

- `used`: evidence that supports a candidate outing date.
- `candidate`: examined evidence that may be relevant but was not used.
- `rejected`: weak evidence, such as screenshot/document-like photos.

Evidence with `should_use=false` is not counted as answer evidence. The UI shows
used, candidate, and rejected evidence separately so weak photo candidates do
not look like grounded answer support.

CLI smoke:

```bash
pma query "2025年12月で出かけたのはいつ？" --config configs/paths.local.yaml
pma query "2025年12月で出かけたのはいつ？" \
  --config configs/paths.local.yaml \
  --temporal-diagnostics
```

Default output includes dates, counts, confidence, reason categories, and
evidence IDs only. It does not print filenames, full paths, GPS coordinates,
raw LINE text, note bodies, OCR text, or full photo captions.

Phase 9-C adds count-only diagnostics for temporal failures. The result reports
the parsed date range (`parsed_date_range_start`, `parsed_date_range_end`), the
parser source, the temporal expression, timezone label, the query column
(`taken_at`), timestamp coverage counts, photo candidate counts before and after
media/annotation filters, removal reason counts, and nearby previous/current/next
month photo counts. These fields make it possible to tell whether a temporal
answer is unknown because photos truly are absent, because `taken_at` is missing,
or because candidates were filtered out.

If photo candidates are missing or weak, PMA searches LINE and notes in the same
date range for configurable outing/event terms such as `出かけ`, `外出`, `駅`,
`旅行`, `食事`, and `予定`. This fallback returns counts and safe evidence IDs
only:

```bash
pma query "2025年12月で出かけたのはいつ？" \
  --config configs/paths.local.yaml \
  --temporal-diagnostics \
  --temporal-fallback-term 外出 \
  --temporal-fallback-term 旅行
```

When photos are absent but LINE/notes support exists, the answer says that photo
evidence was not found and marks the date candidates as weaker text-support
evidence. If all sources are empty, the answer remains unknown and says that no
photos, LINE records, or notes were found in the parsed range.

Temporal photo search depends on `media_items.taken_at`. If imported media rows
do not have capture timestamps yet, run the timestamp audit:

```bash
pma media timestamps audit --config configs/paths.local.yaml
pma media timestamps audit --config configs/paths.local.yaml --month-histogram
```

The audit reports count-only coverage: total media rows, rows with/missing
`taken_at`, existing/missing source files, extractable EXIF/XMP/video/filename
timestamps, optional file-mtime fallback counts, unsupported formats, and parse
errors. It does not print paths or metadata payloads.
For large libraries, extraction probing is sampled by default; use
`--extract-limit 0` only when you intentionally want a deep all-file audit.

Preview backfill before writing:

```bash
pma media timestamps backfill --config configs/paths.local.yaml \
  --dry-run \
  --limit 20 \
  --method auto
```

Backfill is dry-run by default. It reports `dry_run_update_count` and does not
change the database unless `--apply` is explicit. After backing up the local DB
and reviewing the dry-run counts, write SQLite timestamp metadata with:

```bash
pma media timestamps backfill --config configs/paths.local.yaml \
  --limit 100 \
  --method auto \
  --only-missing \
  --apply
```

The apply mode updates SQLite metadata only; original source photos/videos stay
read-only. Verify coverage afterward with `pma media timestamps audit` or a
count query against `media_items.taken_at`.

`exiftool` is preferred when installed because it can read JPEG, HEIC, MOV, MP4,
and XMP metadata. Without `exiftool`, PMA falls back to Pillow for supported
image EXIF. Stored timestamp provenance includes `taken_at_source`,
`taken_at_confidence`, `taken_at_timezone`, and `taken_at_timezone_unknown`.
File modification time is low-confidence and is only used when
`--fallback file-mtime` is explicitly selected. Source files remain read-only.

## Optional Integration Tests

Real embedding model tests are skipped unless explicitly enabled:

```bash
PMA_RUN_REAL_EMBEDDING_TESTS=1 \
PMA_REAL_EMBEDDING_MODEL_PATH=/home/zennakamura/MyApplication/models/embedding/ruri-v3-310m \
pytest -q -m real_embeddings
```

Qdrant tests are skipped unless explicitly enabled:

```bash
PMA_RUN_QDRANT_TESTS=1 \
PMA_QDRANT_URL=http://localhost:6333 \
pytest -q -m qdrant
```

## Privacy

- Indexing output is count-only.
- Search results do not include source file paths or filenames.
- Search snippets are short excerpts, not full documents.
- Excluded rows are not indexed.
- No real personal data is used in tests.

## Future Work

Later phases may add Japanese tokenization, production vector backends, stronger
reranker quality evaluation, and richer evidence-grounded answer generation.
