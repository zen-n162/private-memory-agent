# ExecPlan: Phase 8-A3 Vision Annotation Timeout and Preprocessing

## Goal

Stabilize real local photo annotation against Qwen3-VL by adding longer,
configurable annotation request timeouts and privacy-preserving image
preprocessing before sending images to the local OpenAI-compatible vision
endpoint. Also fix the missing test dependency that currently prevents
`python -m pytest -q` from running in a fresh dev install.

## Non-goals

- Do not run large annotation batches.
- Do not modify, move, rename, delete, or copy original source photos.
- Do not add HEIC/HEIF decoding dependencies.
- Do not send data to external APIs or non-local services by default.
- Do not log filenames, full paths, GPS, EXIF, OCR text, captions, LINE text,
  note bodies, or personal names.

## Current state

`pma models ping vision_common --config configs/paths.local.yaml --vision-smoke`
passes with the local llama.cpp Qwen3-VL endpoint. Real photo annotation reaches
the endpoint but can time out. The current annotation implementation reads the
original image bytes and base64-encodes them directly, so full-resolution images
can be much larger and slower than the synthetic smoke image.

`pyproject.toml` has a `dev` optional dependency group with pytest, but not
`httpx`. FastAPI's TestClient imports require httpx, so a fresh dev install can
still fail test collection.

## Proposed design

1. Add `httpx` to the `dev` optional dependency group.
2. Add optional `request_timeout_seconds` endpoint metadata for longer model
   inference requests while keeping short `/models` ping/preflight timeouts.
3. Add `pma annotate photos --timeout-seconds`, defaulting to 300 seconds when
   config does not specify `request_timeout_seconds`.
4. Preprocess each image before sending it to the vision model:
   - open with Pillow
   - convert to RGB
   - strip EXIF/metadata by creating a new image object
   - resize so longest side is at most `max_side_px`
   - encode as JPEG or PNG bytes
   - send the processed bytes via the existing base64 data URI path
5. Add CLI options:
   - `--max-side-px` default 1280
   - `--image-format jpeg|png` default jpeg
   - `--image-quality` default 90
   - `--check-preprocess` for dry-run preprocessing checks without model calls
6. Extend privacy-safe diagnostics with media item id, safe format/dimensions,
   and whether preprocessing succeeded.

## Data contracts

Photo preprocessing options:

```python
max_side_px: int = 1280
output_format: "jpeg" | "png" = "jpeg"
quality: int = 90
```

Preprocessed image metadata:

```python
mime_type: str
width: int
height: int
original_width: int
original_height: int
source_mime_type: str
```

`PhotoAnnotationErrorDetail` remains privacy-safe and may include:

- `image_format`
- `dimensions`
- `preprocessing_succeeded`

## Files to change

- `pyproject.toml`
- `configs/models.example.yaml`
- `src/private_memory_agent/runtime/clients.py`
- `src/private_memory_agent/annotation/photos.py`
- `src/private_memory_agent/annotation/__init__.py`
- `src/private_memory_agent/cli.py`
- `tests/test_photo_annotation.py`
- `tests/test_runtime_clients.py`
- `docs/MODEL_RUNTIME.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add `httpx` to dev extras and add `Pillow` to runtime dependencies because
   photo annotation preprocessing requires local image decoding/encoding.
2. Add `request_timeout_seconds` to model endpoint metadata and example config.
3. Add annotation preprocessing data classes and functions.
4. Update `annotate_photos` to preprocess before model calls, use preprocessed
   bytes, and support dry-run preprocessing checks.
5. Add CLI options and wire timeout/preprocessing settings into annotation.
6. Update diagnostics formatting without exposing source paths or filenames.
7. Add tests using synthetic images only.
8. Update runtime docs and the Japanese overview where photo processing is
   described.
9. Run verification commands.

## Tests and verification

Run:

```bash
python -m pytest -q
pma annotate photos --help
pma models ping vision_common --config configs/paths.local.yaml --vision-smoke
```

If the local model server is running, run only the bounded real check:

```bash
pma annotate photos \
  --config configs/paths.local.yaml \
  --limit 1 \
  --show-errors \
  --fail-fast \
  --timeout-seconds 300
```

## Privacy and security

Tests use synthetic images under temporary directories. Real source photos are
only read when the user explicitly runs the bounded annotation command.
Preprocessing strips EXIF/metadata and sends resized image bytes to the selected
local endpoint only. CLI output remains count- and id-oriented and does not
print private paths or filenames.

## Performance and hardware

Default `max_side_px=1280` reduces request payload size and model latency on the
RTX 4500 Ada 24GB profile. Recommended modes:

- 1024 for faster checks
- 1280 default
- 2048 for detail-oriented manual runs

No automatic server start or GPU memory allocation is introduced.

## Rollback

Revert the dependency/docs changes and restore annotation to direct source-byte
encoding. This would reintroduce large payloads and likely timeouts for
full-resolution photos.

## Open questions

None blocking.
