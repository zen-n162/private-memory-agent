# ExecPlan: Phase 7-B Local GPU Runtime Profiles

## Goal

Add local operational support for choosing model-serving profiles on the RTX
4500 Ada 24GB machine without starting heavy servers automatically.

## Non-goals

- Do not start llama.cpp, vLLM, Ollama, Docker, or any model server.
- Do not load model files.
- Do not require an actual GPU in unit tests.
- Do not assume all configured model directories exist.
- Do not inspect or print private source data.

## Current state

The repository already has model registry metadata, OpenAI-compatible runtime
clients, endpoint pinging, vision smoke support, and model runtime docs.
`scripts/gpu_check.py` exists but only runs a minimal `nvidia-smi` command.
There is no runtime-profile planner CLI.

Configured hardware in `configs/models.example.yaml` targets an NVIDIA RTX 4500
Ada Generation with 24GB VRAM and a safe model VRAM budget of 21GB.

## Proposed design

Add a small `private_memory_agent.runtime.planning` module with deterministic
runtime profiles:

- `leader_only`
- `vision_batch`
- `japanese_text`
- `lightweight_query`

Each profile declares active model keys, optional model keys, estimated VRAM
requirements, expected providers, and operational notes. The planner combines a
profile with loaded config and optional GPU information, then returns a
serializable plan.

Add `pma runtime plan <profile>`:

- prints which configured models should be active for the requested task
- reports safe VRAM budget and optional fake/queried GPU free memory
- prints endpoint URLs from config
- never starts servers or loads models

Update `scripts/gpu_check.py` to expose reusable parsing and GPU-query helpers
using `nvidia-smi` if available, while remaining safe on machines without NVIDIA
drivers.

Update `docs/MODEL_RUNTIME.md` with recommended profiles and endpoint examples
for llama.cpp, vLLM, and Ollama.

## Data contracts

Runtime planning data classes:

- `GPUInfo`
  - `name`
  - `memory_total_mb`
  - `memory_free_mb`
- `RuntimeProfile`
  - `profile_id`
  - `description`
  - `active_model_keys`
  - `optional_model_keys`
  - `estimated_vram_gb`
  - `notes`
- `RuntimeModelPlan`
  - `model_key`
  - `role`
  - `provider`
  - `enabled`
  - `status`
  - `endpoint_url`
  - `served_model_name`
- `RuntimePlan`
  - profile metadata
  - safe VRAM budget
  - GPU info if available
  - active and optional model plans
  - warnings

CLI:

```bash
pma runtime plan leader_only
pma runtime plan vision_batch --json
pma runtime plan lightweight_query --gpu-free-mb 20000
```

## Files to change

- `.agent/execplans/phase-7b-local-gpu-runtime-profiles.md`
- `src/private_memory_agent/runtime/planning.py`
- `src/private_memory_agent/runtime/__init__.py`
- `src/private_memory_agent/cli.py`
- `scripts/gpu_check.py`
- `tests/test_runtime_planning.py`
- `docs/MODEL_RUNTIME.md`

## Implementation steps

1. Add planning data classes and profile definitions.
2. Add plan builder using config hardware/model registry metadata.
3. Add CLI parser and formatting for `pma runtime plan`.
4. Improve `scripts/gpu_check.py` with parse/query helpers and JSON output.
5. Add unit tests using fake config and fake GPU data.
6. Add docs for profiles and endpoint examples.
7. Run `pytest -q`.

## Tests and verification

- `pytest -q`
- Optional manual checks:
  - `pma runtime plan lightweight_query`
  - `python scripts/gpu_check.py`

Tests use fake GPU data and temporary config. They do not require actual GPU,
model files, network, Docker, or private data.

## Privacy and security

The runtime planner reads only model/config metadata. It does not read source
photos, LINE exports, notes, databases, EXIF, GPS, OCR, or private payloads. It
does not start servers or open public bindings.

## Performance and hardware

The default target is NVIDIA RTX 4500 Ada 24GB. Plans compare estimated profile
VRAM against the configured safe budget, defaulting to 21GB. The planner is
advisory and conservative; it does not guarantee actual runtime memory usage.

## Rollback

Remove the planning module, CLI command, GPU script changes, tests, and docs
updates. Existing model runtime clients and annotation/query commands remain
unchanged.

## Open questions

None blocking. Future phases can refine VRAM estimates from real measured model
server telemetry.
