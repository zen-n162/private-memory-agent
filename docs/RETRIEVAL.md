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

Embedding vectors are persisted in the local `embeddings` table as JSON. This is not a production vector database and does not require Qdrant, FAISS, GPU, or network access by default.

Build local hash embeddings:

```bash
pma index embeddings
pma search semantic "ローカル"
```

Use a real local sentence-transformers-compatible model:

```bash
pma index embeddings --model-backend sentence-transformers --model-key text_embedding
pma search semantic "ローカル" --model-backend sentence-transformers --model-key text_embedding
```

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

Later phases may add Japanese tokenization, production vector backends, reranking, and evidence-grounded answer generation.
