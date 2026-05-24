# ExecPlan: Phase 3-B Fix llama.cpp Multimodal Compatibility

## Goal

Make `pma annotate photos` compatible with the confirmed local llama.cpp
Qwen3-VL OpenAI-compatible multimodal endpoint and add privacy-safe diagnostics
for future annotation failures.

## Non-goals

- Do not run annotation on real private photos.
- Do not read, copy, move, rename, or delete source files except opening selected
  image files read-only during explicit annotation commands.
- Do not add large image conversion dependencies.
- Do not support HEIC/HEIF conversion in this fix.
- Do not send private data to remote endpoints.

## Current state

The local llama.cpp Qwen3-VL server has been confirmed externally by the user:

- `/v1/models` works.
- Text chat completions work.
- Synthetic image chat completions work using OpenAI-compatible `content` array
  parts with `image_url` data URI.

Current PMA state:

- `OpenAICompatibleHTTPClient.analyze()` already sends a chat-completions style
  content array and extracts `choices[0].message.content`.
- CLI vision construction returns the configured model key such as
  `vision_common` as the runtime model name, which may not match the served
  llama.cpp model id.
- `pma annotate photos` swallows per-image errors and prints only counts.
- `--show-errors`, `--fail-fast`, and photo annotation `--dry-run` are absent.
- `pma models ping` cannot target one model by positional key or `--model`.
- There is no synthetic vision smoke command.

The requested `.agent/HTML_OVERVIEW_MAINTENANCE.md` file is not present in this
repository. No `docs/overview_ja.html` file is present either.

## Proposed design

Add a served model name layer to model endpoint metadata:

- `served_model_name` can be configured on a model entry.
- `vision_common` will set `served_model_name:
  Qwen3VL-4B-Instruct-Q4_K_M.gguf`.
- If absent, preflight calls `/v1/models` and selects the configured model id
  only if the server exposes it, otherwise the first returned model id.

Add vision endpoint preflight:

- Validate endpoint URL and API format.
- Call `/v1/models`.
- Resolve served model name.
- Check multimodal capability only when the server returns explicit capability
  metadata.

Add annotation diagnostics:

- Per-image errors are stored as `PhotoAnnotationErrorDetail` with media item id,
  error class, privacy-safe message, and function-only stack summary.
- `--show-errors` prints selected, annotated, errors, model id, endpoint, top
  error classes, truncated example messages, and failed media item ids.
- `--fail-fast` stops after the first per-image annotation error.
- `--dry-run` selects target rows, validates source-file existence and MIME
  support, does not call the endpoint, and does not write annotations.

Add synthetic vision smoke:

- `pma models ping vision_common --config configs/paths.local.yaml --vision-smoke`
- `pma models ping --config configs/paths.local.yaml --model vision_common --vision-smoke`

The smoke request uses a generated 1x1 PNG data URI and writes nothing.

## Data contracts

Config:

```yaml
vision_common:
  served_model_name: Qwen3VL-4B-Instruct-Q4_K_M.gguf
```

Runtime:

- `ModelEndpoint.served_model_name: str | None`
- `VisionEndpointPreflightResult`
- `preflight_vision_endpoint(endpoint, ...)`
- `run_vision_smoke_test(endpoint, ...)`

Photo annotation:

- `PhotoAnnotationResult.dry_run`
- `PhotoAnnotationResult.would_annotate`
- `PhotoAnnotationResult.endpoint_url`
- `PhotoAnnotationResult.error_details`
- `PhotoAnnotationErrorDetail`
- `UnsupportedImageFormat`

CLI:

```bash
pma annotate photos --dry-run
pma annotate photos --show-errors
pma annotate photos --fail-fast
pma models ping vision_common --vision-smoke
pma models ping --model vision_common --vision-smoke
```

## Files to change

- `.agent/execplans/phase-3b-fix-llamacpp-multimodal-compat.md`
- `configs/models.example.yaml`
- `src/private_memory_agent/runtime/clients.py`
- `src/private_memory_agent/runtime/__init__.py`
- `src/private_memory_agent/annotation/photos.py`
- `src/private_memory_agent/annotation/__init__.py`
- `src/private_memory_agent/cli.py`
- `tests/test_runtime_clients.py`
- `tests/test_photo_annotation.py`
- `docs/MODEL_RUNTIME.md`

## Implementation steps

1. Add served model metadata and vision preflight helpers in runtime.
2. Add synthetic vision smoke helper using the same multimodal request format.
3. Add MIME detection and unsupported-format errors in photo annotation.
4. Add dry-run, fail-fast, and error aggregation support to annotation.
5. Update CLI flags and output formatting.
6. Update model ping to accept a positional model key and `--model`.
7. Add `--vision-smoke` to model ping.
8. Set `vision_common.served_model_name` and the local endpoint in config.
9. Add tests with monkeypatched/fake HTTP transports and synthetic files only.
10. Update model runtime troubleshooting docs.
11. Run `pytest -q`, `pma models ping --help`, and
    `pma annotate photos --help`.

## Tests and verification

Required:

- `pytest -q`
- `pma models ping --help`
- `pma annotate photos --help`

Targeted tests:

- OpenAI-compatible multimodal request uses content array.
- Request includes `image_url`.
- Image URL is `data:<mime>;base64,...`.
- Served model name is used instead of `vision_common`.
- Response parser extracts `choices[0].message.content`.
- `/v1/models` preflight succeeds.
- Unavailable endpoint fails before image loop.
- Unsupported image format is reported safely.
- Annotation success writes one annotation.
- Annotation failure aggregates error classes.
- `--fail-fast` stops after first error.
- `--show-errors` does not leak private paths.
- Synthetic smoke test uses synthetic image data only.

## Privacy and security

Diagnostics must never print full file paths, filenames, EXIF, GPS, OCR text,
raw captions, private metadata, or source payloads. Error reports use media item
ids and sanitized messages. The synthetic smoke command generates its own image
and does not require an ingestion database.

The runtime client still rejects non-local endpoints unless `--allow-remote` is
explicitly passed.

## Performance and hardware

Default tests use fake HTTP transport and tiny synthetic images. No GPU or VRAM
is required. Real Qwen3-VL usage is manual and depends on the already-running
local llama.cpp server.

## Rollback

Remove the new CLI flags, runtime helpers, annotation diagnostics, tests, config
field, and docs changes. Existing fake annotation tests and basic runtime client
behavior should remain usable.

## Open questions

None blocking. HEIC/HEIF conversion remains intentionally out of scope unless a
small existing dependency is later adopted.
