# ExecPlan: Phase 8-A Runtime Transport Stabilization

## Goal

Fix the OpenAI-compatible HTTP runtime client so local endpoint calls pass
timeouts correctly and never accidentally treat timeout values as request body
data. Runtime commands such as `pma models ping ... --vision-smoke` and
`pma annotate photos ... --show-errors --fail-fast` should produce controlled,
privacy-safe output instead of Python tracebacks.

## Non-goals

- Do not start model servers automatically.
- Do not run large photo annotation batches.
- Do not load model weights in unit tests.
- Do not ingest, inspect, or copy private source data.
- Do not change the OpenAI-compatible request schema except where needed for
  transport correctness and diagnostics.

## Current state

`src/private_memory_agent/runtime/clients.py` defines `HTTPTransport` as a
callable accepting `(Request, float)`, but `OpenAICompatibleHTTPClient` stores
the default transport as `urllib.request.urlopen` directly. The client calls
`self._transport(request, self.timeout_seconds)`. For `urlopen`, the second
positional argument is `data`, not `timeout`, so a float timeout can be treated
as a request body. This breaks `/models`, chat completions, vision smoke, and
photo annotation paths that use the default transport.

Existing runtime tests use fake transports with the intended two-argument
signature, so they do not catch the default `urlopen` mismatch.

## Proposed design

Keep PMA's internal transport signature as `(request, timeout_seconds)` and add
an explicit wrapper around `urllib.request.urlopen`:

```python
def default_http_transport(request: Request, timeout_seconds: float) -> Any:
    return urlopen(request, timeout=timeout_seconds)
```

`OpenAICompatibleHTTPClient` will use this wrapper by default. Tests can still
inject fake transports with the same PMA transport contract.

Broaden runtime error conversion enough that unexpected transport type errors
become safe `ModelRuntimeError` messages instead of escaping to the CLI as
tracebacks.

## Data contracts

- `HTTPTransport`: callable taking `urllib.request.Request` and `float`, and
  returning a context-manager response with `read()` plus `status` or `code`.
- GET `/models` must have no request body.
- POST `/chat/completions` must send JSON body and preserve the multimodal
  OpenAI-compatible content array with `image_url` data URIs.

## Files to change

- `src/private_memory_agent/runtime/clients.py`
- `tests/test_runtime_clients.py`
- `tests/test_photo_annotation.py` if CLI annotation regression coverage needs
  expansion
- `docs/MODEL_RUNTIME.md`

`docs/overview_ja.html` is not expected to change because this is an internal
runtime bug fix, not a behavior/architecture/status change in the overview.

## Implementation steps

1. Add an explicit runtime transport protocol/type and default wrapper that
   calls `urlopen(request, timeout=timeout_seconds)`.
2. Update `OpenAICompatibleHTTPClient` to use the wrapper by default.
3. Add safe error conversion for unexpected transport-level `TypeError` and
   related runtime exceptions.
4. Add regression tests proving the default wrapper passes timeout as a keyword
   and no body is sent for GET `/models`.
5. Add tests covering fake transport ping, synthetic vision smoke, preflight,
   multimodal annotation request shape, fail-fast behavior, and privacy-safe
   output where gaps remain.
6. Add a troubleshooting note to `docs/MODEL_RUNTIME.md`.
7. Run required verification commands.

## Tests and verification

Run:

```bash
pytest -q
pma models ping --help
pma annotate photos --help
```

If the local Qwen3-VL server is running, optionally run only bounded live
checks:

```bash
pma models ping vision_common --config configs/paths.local.yaml --vision-smoke
pma annotate photos --config configs/paths.local.yaml --limit 1 --show-errors --fail-fast
```

Unit tests must not require GPU, models, private data, Docker, or network
access.

## Privacy and security

Transport errors must be converted into generic PMA runtime errors that do not
include private filenames, full paths, GPS, EXIF, OCR text, LINE text, note
bodies, or personal names. Tests use temporary directories and synthetic images
only.

## Performance and hardware

No GPU or VRAM assumptions are introduced. This fix only changes HTTP request
dispatch and diagnostics. The RTX 4500 Ada 24GB runtime profiles remain
advisory and manual.

## Rollback

Revert the transport wrapper, tests, and documentation note. This returns the
client to direct `urlopen` use, but would reintroduce the float-timeout body bug.

## Open questions

None blocking.
