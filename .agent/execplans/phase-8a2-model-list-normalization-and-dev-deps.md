# ExecPlan: Phase 8-A2 Model List Normalization and Dev Dependencies

## Goal

Fix OpenAI-compatible `/v1/models` parsing so PMA correctly understands both
standard OpenAI-style responses and llama.cpp responses that expose model
capabilities under the top-level `models` field. Also define the development
dependency path so `python -m pytest -q` works after installing the project with
dev extras.

## Non-goals

- Do not start model servers automatically.
- Do not run photo annotation batches.
- Do not inspect, ingest, copy, or print private source data.
- Do not load real model weights in unit tests.
- Do not change the synthetic vision smoke image behavior.

## Current state

`src/private_memory_agent/runtime/clients.py` currently extracts model ids from
`raw["data"]` only and checks multimodal capability from the matching `data`
item. llama.cpp can return both:

- `data`: OpenAI-style model records with `id`
- `models`: llama.cpp-style model records with `name`, `model`, and
  `capabilities`

When `data[0]` has `meta` but not `capabilities`, PMA can incorrectly classify
the served model as non-multimodal even though `models[0].capabilities` contains
`multimodal`.

`pyproject.toml` has runtime dependencies and pytest config, but no
`[project.optional-dependencies]` dev group containing pytest.

## Proposed design

Add a private normalized model record data class and a normalization function
that merges records from both `data` and `models`. The normalized record will
carry:

- `id`
- `name`
- `model`
- `capabilities`
- `raw`

Served model resolution will match against any normalized alias
(`id`, `name`, or `model`). Vision preflight will require multimodal only when
explicit capability metadata exists. If capability metadata is absent, preflight
will continue with a warning instead of falsely failing.

Add `dev = ["pytest>=8"]` to `pyproject.toml`, and document:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
```

## Data contracts

Normalized model record:

```python
id: str
name: str | None
model: str | None
capabilities: tuple[str, ...]
raw: dict[str, Any]
```

`VisionEndpointPreflightResult` and `VisionSmokeResult` may include warnings
when capability metadata is absent. Warnings must be safe and must not include
private paths or user content.

## Files to change

- `src/private_memory_agent/runtime/clients.py`
- `tests/test_runtime_clients.py`
- `pyproject.toml`
- `README.md`
- `docs/MODEL_RUNTIME.md`
- `docs/overview_ja.html` only for the developer command wording required by
  the overview maintenance rule

## Implementation steps

1. Add normalized model record helpers.
2. Replace id extraction and multimodal capability checks with normalized
   records.
3. Add non-strict warning behavior for missing capability metadata.
4. Preserve strict failure when explicit capabilities exist but do not include
   multimodal/image/vision support.
5. Add regression tests for OpenAI-style, llama.cpp-style, only-`models`,
   only-`data`, alias matching, missing capabilities, and privacy-safe CLI
   output.
6. Add the dev optional dependency group and update install/test docs.
7. Run verification commands.

## Tests and verification

Run:

```bash
python -m pytest -q
pma models ping --help
pma models ping vision_common --config configs/paths.local.yaml --vision-smoke
```

If the local Qwen3-VL server is unavailable, report the controlled failure
instead of treating it as a task failure.

## Privacy and security

Unit tests use fake transports and synthetic responses only. The live smoke
command sends only a synthetic image. No private photo filenames, LINE text,
note bodies, GPS, EXIF, OCR text, source directories, or personal names are
logged or added to fixtures.

## Performance and hardware

No GPU, VRAM, or model-loading behavior changes. Runtime profile guidance for
the RTX 4500 Ada 24GB remains advisory.

## Rollback

Revert the normalization helpers, tests, dependency/docs changes, and warning
fields. This would restore the previous behavior, including the false
multimodal failure for llama.cpp responses with capabilities under `models`.

## Open questions

None blocking.
