# Model Runtime

This document describes the runtime environment for model inference.

## Topics

- model loading
- device management
- performance monitoring

## Runtime Clients

Phase 3-A adds model runtime client abstractions. App logic should depend on these interfaces instead of importing llama.cpp, vLLM, Ollama, or Transformers directly:

- `ChatModelClient`
- `VisionModelClient`
- `RerankerClient`

The runtime package also includes deterministic fake clients for tests and a stdlib HTTP client for local OpenAI-compatible endpoints:

- llama.cpp server OpenAI-compatible mode
- vLLM OpenAI-compatible server
- Ollama OpenAI-compatible `/v1` endpoints

Configured endpoint metadata lives on model entries in `configs/models.example.yaml`:

```yaml
leader:
  provider: llama_cpp
  role: leader_reasoning
  model_dir: deepseek/DeepSeek-R1-0528-Qwen3-8B-GGUF
  api_format: openai-compatible
  endpoint_url: http://127.0.0.1:8080/v1
  timeout_seconds: 2
  request_timeout_seconds: 300
  retries: 0
```

Ping configured local endpoints without sending prompts:

```bash
pma models ping
pma models ping --json
pma models ping vision_common
pma models ping --model vision_common
```

`pma models ping` calls `/models` only. It does not send LINE messages, note bodies, image metadata, GPS, filenames, or source paths. Non-local endpoint URLs are rejected by default unless explicitly allowed with `--allow-remote`.

## Leader-Guided Retrieval Planning

Phase 8-K can use the configured local leader endpoint before retrieval to
create a structured retrieval plan:

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only --query-id qst_preparation --leader-plan --json
```

This sends the private question text to the configured local leader model only.
It does not send source files or raw evidence snippets for planning. The default
report hides the full plan and prints only counters: plan created, retrieval
query count, entity/concept counts, source preferences, source constraints, and
acceptance-criteria count. `--show-plan` is explicit and should be treated as
private local output.

Plan-aware relevance judging and repair are optional:

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only --query-id qst_preparation --leader-plan --leader-rerank --retrieval-repair 1 --json
```

The default judge is deterministic and local. It can demote generic-only
evidence, promote evidence matching specific plan concepts, and expose safe
relevance metadata when `--show-relevance` is used. Full leader-based evidence
judging can be added later; it should remain opt-in because sending candidate
snippets to a model is slower and more privacy-sensitive.

## RTX 4500 Ada Runtime Profiles

Phase 7-B adds advisory runtime profiles for the local NVIDIA RTX 4500 Ada
24GB machine. PMA prints plans only; it does not start heavy model servers.

Inspect a profile:

```bash
pma runtime plan lightweight_query
pma runtime plan vision_batch --json
```

Use fake/manual GPU memory numbers for planning checks:

```bash
pma runtime plan vision_batch \
  --gpu-name "NVIDIA RTX 4500 Ada Generation" \
  --gpu-total-mb 24576 \
  --gpu-free-mb 22000
```

Recommended profiles:

| profile | active models | estimate | use |
| --- | --- | ---: | --- |
| `leader_only` | `leader` | 13GB | grounded answer generation |
| `vision_batch` | `vision_common` | 14GB | photo annotation batches |
| `japanese_text` | `japanese_text` | 12GB | LINE and notes extraction |
| `lightweight_query` | `leader`, `text_embedding` | 16GB | interactive local RAG |

The configured safe model budget is 21GB by default, leaving room for desktop,
CUDA overhead, KV cache growth, SQLite, and Python processes. Treat these
numbers as conservative planning estimates, not measured guarantees.

Operational guidance:

- Keep only one heavyweight profile active at a time on 24GB VRAM.
- Prefer `vision_batch` for annotation windows, then stop the vision server.
- Prefer `lightweight_query` for interactive querying.
- Use `leader_only` when retrieval is already built and embeddings are not
  needed.
- Use `japanese_text` when extracting structured metadata from LINE or notes.
- Start and stop servers manually in a separate shell or service manager.

Check local GPU memory without requiring tests to have a GPU:

```bash
python scripts/gpu_check.py
python scripts/gpu_check.py --json
```

## Endpoint Configuration Examples

llama.cpp OpenAI-compatible server:

```yaml
leader:
  provider: llama_cpp
  role: leader_reasoning
  model_dir: deepseek/DeepSeek-R1-0528-Qwen3-8B-GGUF
  api_format: openai-compatible
  endpoint_url: http://127.0.0.1:8080/v1
  served_model_name: DeepSeek-R1-0528-Qwen3-8B-UD-Q4_K_XL.gguf
  timeout_seconds: 2
  request_timeout_seconds: 300
  retries: 0
```

Example llama.cpp leader server shape, assuming the model file already exists
locally and you have chosen the exact `.gguf` file:

```bash
llama-server \
  --host 127.0.0.1 \
  --port 8080 \
  --model /path/to/DeepSeek-R1-0528-Qwen3-8B-UD-Q4_K_XL.gguf \
  --ctx-size 8192
```

PMA does not start this process automatically and does not download the model.
Keep the configured `served_model_name` aligned with `/v1/models` when the
server exposes a specific file name.

vLLM OpenAI-compatible server:

```yaml
japanese_text:
  provider: vllm
  role: japanese_line_notes
  model_dir: qwen/Qwen3-Swallow-8B-RL-v0.2-AWQ-INT4
  api_format: openai-compatible
  endpoint_url: http://127.0.0.1:8000/v1
  served_model_name: Qwen3-Swallow-8B-RL-v0.2-AWQ-INT4
  timeout_seconds: 2
  retries: 0
```

Ollama OpenAI-compatible endpoint:

```yaml
lightweight_local:
  provider: ollama
  role: lightweight_query
  model_dir: ollama/local-model
  api_format: openai-compatible
  endpoint_url: http://127.0.0.1:11434/v1
  served_model_name: qwen3:8b
  timeout_seconds: 5
  retries: 0
```

These examples are metadata only. PMA does not download models and does not
start the corresponding server processes.

## Photo Annotation Models

Phase 3-B adds `pma annotate photos`, which uses the `VisionModelClient` interface to generate derived image annotations for imported `media_items`.

Default safe smoke path:

```bash
pma annotate photos --client fake
```

Real local vision path, assuming the configured local server is already running:

```bash
pma annotate photos --client openai-compatible --model-key vision_common --timeout-seconds 300
```

Check the configured endpoint and run a synthetic image smoke without touching
the photo database:

```bash
pma models ping vision_common --config configs/paths.local.yaml
pma models ping vision_common --config configs/paths.local.yaml --vision-smoke
```

Expected local roles:

- `vision_common`: Qwen3-VL style visual captioning and object descriptions.
- `vision_heavy`: larger Qwen3-VL style model for slower or harder images.
- Florence-style detector/OCR server: optional future specialized object detection or dense captioning role.
- PaddleOCR-style OCR server: optional future text extraction role when OCR quality matters.

The command stores one `media_annotations` row per image with:

- caption in `value_text`
- objects and OCR text in `data_json`
- confidence if the model provides it
- model name in `model_id`

The command prints count-only summaries. It does not print filenames, OCR text,
GPS, face data, or full model outputs. Source images remain read-only and are
sent only to the explicitly selected local vision client.

Before sending a real photo to the vision client, PMA preprocesses it locally:

- opens it read-only with Pillow
- converts to RGB
- strips EXIF and other image metadata by re-encoding
- resizes so the longest side is at most `--max-side-px`
- encodes the processed image as JPEG by default

The source file is not modified. Defaults are `--max-side-px 1280`,
`--image-format jpeg`, `--image-quality 90`, and `--timeout-seconds 300` for
real photo annotation. Use `--check-preprocess` with `--dry-run` to verify local
image preprocessing without model calls or annotation writes.

### llama.cpp Qwen3-VL Troubleshooting

If `/v1/models` succeeds and a direct synthetic image curl or Python request
succeeds, but `pma annotate photos` fails, check PMA request formatting,
served-model-name resolution, MIME handling, and response parsing.

If real photo annotation times out while the synthetic vision smoke succeeds,
the original image may be too large for the current local serving profile.
Use preprocessing/resizing and a longer request timeout before widening a batch.
Recommended `--max-side-px` values for the RTX 4500 Ada 24GB machine:

- `1024`: fast mode
- `1280`: default
- `2048`: detail mode for slower manual checks

If a traceback or diagnostic mentions a float request body, for example
`message_body should be a bytes-like object or an iterable, got <class 'float'>`,
check HTTP transport timeout handling. OpenAI-compatible endpoint calls must
pass urllib timeouts as `timeout=<seconds>` or through PMA's transport wrapper;
the second positional argument to `urllib.request.urlopen` is request body data,
not timeout.

If `/v1/models` shows `capabilities: ["completion", "multimodal"]` but PMA
says multimodal capability is missing, check model-list normalization. llama.cpp
may report capability metadata under the top-level `models` array while
OpenAI-style model ids are under `data`.

For llama.cpp multimodal OpenAI-compatible requests, use:

- endpoint: `/v1/chat/completions`
- request content: a chat `messages` array
- user message content: an array containing text and `image_url` parts
- image value: `data:<mime>;base64,<base64>`
- response text: `choices[0].message.content`

The request body shape is:

```json
{
  "model": "Qwen3VL-4B-Instruct-Q4_K_M.gguf",
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "この画像を日本語で簡単に説明してください。"},
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/png;base64,<base64>"
          }
        }
      ]
    }
  ],
  "max_tokens": 512,
  "temperature": 0.2
}
```

Start with a single item and diagnostics:

```bash
pma models ping vision_common --config configs/paths.local.yaml --vision-smoke

pma annotate photos \
  --config configs/paths.local.yaml \
  --limit 1 \
  --show-errors \
  --fail-fast \
  --timeout-seconds 300
```

Then widen the batch:

```bash
pma annotate photos \
  --config configs/paths.local.yaml \
  --limit 5 \
  --show-errors
```

Use dry-run first when checking selection and local file/MIME handling without
calling the model or writing annotations:

```bash
pma annotate photos \
  --config configs/paths.local.yaml \
  --limit 5 \
  --dry-run \
  --show-errors
```

Inspect batch status without opening source files or calling the model:

```bash
pma stats --config configs/paths.local.yaml
pma annotate photos --status --config configs/paths.local.yaml
pma annotate photos --failed --config configs/paths.local.yaml
```

These status commands report aggregate counts, model-id breakdowns, latest
annotation timestamps, and safe media item ids for tracked failures. They do not
print filenames, full paths, GPS, EXIF, OCR text, captions, or image content.

## Real-Data E2E Smoke

Phase 8-C adds a read-only smoke workflow for checking that existing local
metadata can move through counts, index checks, retrieval, evidence packing, and
structured answer validation without exposing private content.

Recommended sequence:

```bash
pma stats --config configs/paths.local.yaml
pma index text --config configs/paths.local.yaml
pma e2e smoke --config configs/paths.local.yaml --dry-run
pma e2e smoke --config configs/paths.local.yaml --retrieval-only
pma e2e smoke --config configs/paths.local.yaml --retrieval-only --no-fallback
pma e2e smoke --config configs/paths.local.yaml --retrieval-only --diagnose --json
pma e2e smoke --config configs/paths.local.yaml --fake-model
pma models ping leader --config configs/paths.local.yaml
pma models ping leader --config configs/paths.local.yaml --chat-smoke --max-tokens 64 --timeout-seconds 300
pma models ping leader --config configs/paths.local.yaml --json-smoke --max-tokens 128 --timeout-seconds 300
pma e2e smoke --config configs/paths.local.yaml --real-model --query-limit 1 --timeout-seconds 600 --max-tokens 512 --json
```

Default smoke behavior is privacy-safe and lightweight. It does not ingest new
data, annotate photos, modify source files, or start model servers. Human and
JSON output contain aggregate counts, source types, safe evidence ids, and
status flags only. Query text, snippets, filenames, full paths, GPS, EXIF, OCR,
raw LINE text, note bodies, and full captions are not printed.

Use `--fake-model` first. It checks retrieval, evidence packing, strict answer
schema validation, and Evidence Critic behavior without a model server. Once
fake-model smoke passes, use `pma models ping leader --chat-smoke` to confirm
that generation works, then use `--real-model --query-limit 1` to make a single
local leader request. `--query-id <id>` can select one configured smoke query
from `configs/e2e_smoke_queries.local.yaml` or the example profile. Labels such
as `query_1` are also accepted.

Real-model E2E performs a leader endpoint preflight before sending any prompt:

- reads the configured leader model entry
- calls `/v1/models`
- resolves the served model name
- fails early with a sanitized status if the endpoint is unreachable

DeepSeek-style reasoning models may return plain JSON, fenced JSON, Japanese
text before or after JSON, or `<think>...</think>` reasoning blocks. PMA strips
`<think>` blocks and extracts the first valid JSON object conservatively. The
real-model prompt requires the first character to be `{`, the last character to
be `}`, and lists the allowed evidence ids and source labels explicitly. Add
`--response-format-json` when the local endpoint supports OpenAI-compatible
`response_format={"type":"json_object"}`; PMA falls back without it for
unsupported status responses. After parsing, PMA validates the strict answer
schema and evidence ids.
Extraction strategy is reported as `direct_json`, `fenced_json`,
`extracted_object`, `retry_success`, or `failed` without printing raw model
output. If you see `AnswerValidationError`, inspect:

- whether `evidence_references` exactly match printed safe evidence ids
- whether `used_sources` are backed by referenced evidence
- whether weak evidence has explicit unknowns and low confidence
- whether the model wrapped or malformed the JSON beyond what PMA can extract
- whether a reasoning model exhausted `--max-tokens` before emitting final JSON

The error message is sanitized and does not include evidence text. Safe metadata
includes response length, JSON-like braces, fenced JSON detection, `<think>` tag
detection, extraction attempts, retry status, allowed evidence count, and
allowed source labels. `--show-model-output-metadata` is safe to use before
considering raw output.
Real-model E2E retries once by default after invalid JSON with a shorter repair
prompt that does not resend full evidence. Tune this with `--json-retry`.
`--show-model-output` is available for debugging but may reveal
evidence-derived private content; keep it off unless you explicitly need a
truncated raw model preview.

Phase 8-G adds safe answer inspection. By default, E2E smoke still hides the
answer conclusion and unknown text because model answers may summarize private
evidence. Use `--show-answer` only for local review:

```bash
pma e2e smoke \
  --config configs/paths.local.yaml \
  --real-model \
  --query-limit 1 \
  --timeout-seconds 600 \
  --max-tokens 512 \
  --show-answer
```

`--show-answer` displays only the structured answer fields: conclusion,
confidence, evidence references, used sources, and unknowns. It does not show
raw evidence snippets. A separate `--show-snippets` flag can display truncated
local evidence snippets for debugging; treat that output as private and do not
paste it into public chats. The JSON report also includes an `answer_audit`
section with counts for successful answers, validation errors, retry usage,
average confidence, evidence-reference coverage, unknown evidence references,
and answer source coverage.

For repeatable answer-quality review, use golden questions. The safe template
is `configs/golden_questions.example.yaml`; private local questions belong in
`configs/golden_questions.local.yaml`, which is ignored by Git. Golden
evaluation reuses the same retrieval, evidence packing, leader model, JSON
validation, and privacy controls as E2E smoke:

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only --query-limit 2 --json
pma eval golden --config configs/paths.local.yaml --fake-model --query-limit 2 --json
pma eval golden --config configs/paths.local.yaml --real-model --query-limit 1 --timeout-seconds 600 --max-tokens 512 --json
```

Default golden reports hide question text and answer text. Use `--show-answer`
only for local review. Use `--output` for Markdown reports with manual rating
placeholders and `--output-jsonl` for later machine-readable tracking. Treat
reports generated with `--show-answer` or `--show-snippets` as private.

When real-model answers keep returning `unknown`, first check golden retrieval
source coverage. Constrain one question before blaming the leader model:

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only \
  --query-id qst_preparation \
  --require-source line \
  --require-source notes \
  --exclude-source photos \
  --json
```

If needed, inspect local snippets explicitly:

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only \
  --query-id qst_preparation \
  --require-source line \
  --require-source notes \
  --exclude-source photos \
  --show-snippets \
  --snippet-chars 120
```

Snippet output may contain private local content. Keep it local and do not
paste it into public chats.

Real-model E2E uses a compact redacted evidence packet by default. Start with:

```bash
pma e2e smoke \
  --config configs/paths.local.yaml \
  --real-model \
  --query-limit 1 \
  --timeout-seconds 600 \
  --max-tokens 512 \
  --max-evidence-items 3 \
  --max-evidence-chars 2000 \
  --json-retry 1 \
  --json
```

If generation times out, the smoke report includes safe diagnostics such as the
endpoint, served model id, timeout, max tokens, prompt character count, and
evidence item count sent to the model. It does not print the evidence text.
If a DeepSeek-style reasoning model reaches `AnswerValidationError` at
`--max-tokens 256` while retrieval succeeded, retry the same one-query smoke
with a larger cap such as `--max-tokens 512` or `--max-tokens 1024` before
widening the batch.

After new photo annotation batches, rerun `pma index text`. If
`media_annotations_count` is greater than `media_annotations_in_text_index_count`,
E2E smoke warns that photo annotation text indexing is behind latest
annotations.

E2E smoke success does not by itself prove retrieval quality is good. Always
check whether evidence came from configured smoke queries or from the marked
inventory fallback:

```bash
pma e2e smoke --config configs/paths.local.yaml --retrieval-only --no-fallback --json
pma e2e smoke --config configs/paths.local.yaml --retrieval-only --diagnose --json
pma retrieve audit --config configs/paths.local.yaml --json
```

Schema-aware diagnostics clarify whether counts are physical table counts or
derived logical counts. PMA's text index table is named
`text_search_documents`; do not assume a table named `text_documents` exists.
Inspect actual local schema with:

```bash
pma db schema --config configs/paths.local.yaml
```

Photo annotation diagnostics distinguish direct media annotation retrieval from
text-index or embedding coverage. If `media_annotations_searchable_via` contains
`direct_media_annotation_retrieval`, photo annotations can be retrieved even if
they are not present in `text_search_documents`.

Phase 8-D also indexes photo annotation text into `text_search_documents` as
`source_table=media_items` when `pma index text` is run. That lets regular text
retrieval and retrieval audit stage counts see photo captions, object tags,
summaries, and OCR-derived text without printing those private payloads.

Japanese smoke queries often use full natural-language questions, while SQLite
FTS5 is not a Japanese morphological tokenizer. PMA keeps FTS5 enabled but also
uses a deterministic keyword LIKE fallback over normalized local text. Retrieval
audit reports `fts_candidate_count`, `exact_like_candidate_count`, and
`keyword_like_candidate_count` so a zero-evidence result shows which stage failed.

The query profile lives in:

```bash
configs/e2e_smoke_queries.example.yaml
```

Local ignored overrides can be placed in:

```bash
configs/e2e_smoke_queries.local.yaml
```

Keep local smoke queries generic when possible. The smoke report intentionally
does not echo query text because a local override may contain sensitive words.

Diagnostics are privacy-safe by default. They report counts, endpoint, error
classes, truncated safe messages, and failed media item ids. They do not print
filenames, full paths, GPS, EXIF, OCR text, captions, or private metadata.

Supported direct image MIME types for Phase 3-B-fix:

- `.jpg` / `.jpeg`: `image/jpeg`
- `.png`: `image/png`
- `.webp`: `image/webp`

HEIC/HEIF files are skipped with `UnsupportedImageFormat` unless a future phase
adds explicit local conversion support.

## Japanese Text Understanding

Phase 3-C adds `pma annotate text --source line|notes`, which extracts structured metadata from imported LINE messages or notes.

Default fake smoke path:

```bash
pma annotate text --source line --client fake
pma annotate text --source notes --client fake
```

Real local Japanese text path, assuming the configured local server is already running:

```bash
pma annotate text --source line --client openai-compatible --model-key japanese_text
```

Expected local role:

- `japanese_text`: Qwen3-Swallow or another Japanese-capable local chat model that can return strict JSON.

The model response must be a single JSON object with:

- entities
- topics
- dates
- action items
- event hints
- summary
- confidence

Malformed JSON, missing keys, extra top-level keys, invalid list/object shapes, or confidence values outside `0..1` are rejected. Rejected rows are counted as errors rather than partially trusted.

The command stores validated derived metadata in `text_annotations`. It does not overwrite `line_messages.body_text`, `notes.title`, or `notes.body_text`. CLI output is count-only and does not print raw text or extracted names/topics.

## Text Embeddings

Real text embeddings are optional. Default unit tests and basic CLI checks use fake or deterministic hash embeddings and do not load model files.

Configured local model candidates:

- `text_embedding`: `embedding/ruri-v3-310m`
- `text_embedding_ruri_130m`: `embedding/ruri-v3-130m`
- `text_embedding_bge_m3`: `embedding/bge-m3`
- `text_embedding_qwen_06b`: `qwen/Qwen3-Embedding-0.6B`
- `text_reranker`: `reranker/ruri-v3-reranker-310m`
- `text_reranker_qwen_06b`: `qwen/Qwen3-Reranker-0.6B`

Use real local embeddings only when the model directory already exists under the configured model root:

```bash
pma index embeddings --config configs/paths.local.yaml \
  --model ruri-v3-310m \
  --source line \
  --source notes \
  --skip-existing
pma search semantic "ローカル" --config configs/paths.local.yaml --model ruri-v3-310m
```

The SentenceTransformers adapter imports heavy libraries lazily, sets offline environment defaults, and rejects missing model paths. It does not download models.
Install the optional local model runtime dependencies only when needed:

```bash
python -m pip install -e ".[local-models]"
```

Qdrant is optional and must already be running:

```bash
pma index embeddings --vector-store qdrant --qdrant-url http://localhost:6333
pma search semantic "ローカル" --vector-store qdrant --qdrant-url http://localhost:6333
```

Do not start Docker automatically from this app.

## Semantic Retrieval In Smoke And Golden Evaluation

Phase 8-M can use persisted local embeddings in E2E smoke and golden
evaluation. Phase 8-N adds real embedding aliases and optional local rerankers.
This is optional and local-only:

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only \
  --query-id qst_preparation \
  --leader-plan \
  --leader-rerank \
  --retrieval-repair 1 \
  --semantic \
  --semantic-model ruri-v3-310m \
  --reranker none \
  --json
```

`--semantic` uses the deterministic hash embedding adapter, so it only works
well when embeddings were indexed with the matching hash backend:

```bash
pma index embeddings --config configs/paths.local.yaml --model-backend hash
```

Controls:

- `--semantic-model MODEL`: `hash`, `fake`, `ruri-v3-310m`,
  `ruri-v3-130m`, `bge-m3`, or `qwen3-embedding-0.6b`.
- `--semantic-top-k N`: semantic candidate limit before merge/ranking.
- `--semantic-weight FLOAT`: score multiplier for semantic candidates.
- `--reranker MODEL`: `none`, `fake`, `ruri-v3-reranker-310m`, or
  `qwen3-reranker-0.6b`.
- `--rerank-top-k N`: number of top candidates to rerank locally.
- `--no-semantic`: keep retrieval text-only.

Reports show `semantic_candidate_count`, `semantic_embedding_model_id`,
`reranker_model_id`, and safe counters only. They do not print raw LINE text,
note bodies, captions, filenames, paths, GPS, EXIF, OCR, raw embedding input, or
full repair queries by default. Real sentence-transformer semantic retrieval
requires explicit indexing/search commands and optional integration tests;
default unit tests stay model-free.

## Semantic Comparison And Device Diagnostics

Phase 8-O adds a quality comparison workflow:

```bash
pma eval semantic-compare --config configs/paths.local.yaml \
  --query-id qst_preparation \
  --json
```

This compares text-only, hash semantic, real semantic, real semantic plus
reranker, leader-planned semantic, and leader-planned semantic plus reranker
configurations. A reranker-only run can improve ordering, but it does not prove
evidence quality unless leader-plan relevance judging also runs. Reports mark
those configurations as `quality_judged=false`.

If PyTorch emits a warning like `NVIDIA driver on your system is too old`,
SentenceTransformers may fall back to CPU or fail to use CUDA correctly. Use CPU
explicitly until the driver/runtime stack is fixed:

```bash
pma eval semantic-compare --config configs/paths.local.yaml \
  --query-id qst_preparation \
  --embedding-device cpu \
  --json
```

The comparison report includes `embedding_device_status` with the requested
device, selected device, CUDA availability if inspectable, warning detection if
captured, and a recommendation. This is diagnostic only; PMA still never starts
model servers or downloads model files automatically.
