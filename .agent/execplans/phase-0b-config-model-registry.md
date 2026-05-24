# ExecPlan: Phase 0-B Configuration and Model Registry

## Goal

Create a robust local configuration layer and model registry for Private Memory Agent. The application should load typed settings from YAML plus environment variable overrides, discover configured local model directories without loading weights, and expose `pma config show`, `pma models list`, and expanded `pma doctor` checks.

## Non-goals

- Do not load model weights or import heavyweight model runtimes.
- Do not download, copy, move, hash, or inspect model files.
- Do not implement ingestion, retrieval, embedding creation, API serving, UI, or agent orchestration.
- Do not require the real `/home/zennakamura/MyApplication/models` directory in unit tests.
- Do not add real personal data or hard-code private source data paths.

## Current state

Phase 0-A created a Python 3.11 `src/` package, `pma` CLI, a small config loader, example configs, and basic tests. `configs/models.example.yaml` defines model roles and directories under a configurable root. `pma doctor` currently checks Python version, example config presence, privacy defaults, and the configured model root string, but it does not validate directories, CUDA visibility, `nvidia-smi`, or data directory writability. There is no model registry object and no `pma models list` or `pma config show` command.

## Proposed design

Keep Phase 0-B dependency-free and use standard-library dataclasses as typed settings models. Extend the config loader to return:

- `AppSettings`
- `PathSettings`
- `HardwareSettings`
- `ModelSpec`
- `ModelRegistry`
- `ConfigBundle`

The loader will parse the repository's simple YAML mapping format, apply safe environment overrides such as `PMA_CONFIG_DIR`, `PMA_MODEL_ROOT`, `PMA_APP_DATA_DIR`, `PMA_ENV`, `PMA_ALLOW_NETWORK`, and `PMA_LOG_PRIVATE_DATA`, and normalize paths. The model registry will expose configured models with resolved paths and existence metadata, but it will not read model files or load runtimes.

The CLI will gain:

- `pma config show`: print non-secret resolved settings.
- `pma models list`: print configured model roles, provider, enabled state, path, and directory status.
- Expanded `pma doctor`: check Python, optional CUDA visibility, optional `nvidia-smi`, model root existence, configured model directories, and app data directory writability.

## Data contracts

- `AppSettings`: application name, environment, privacy mode, network flag, private logging flag, default timezone.
- `PathSettings`: model root, app data directory, and future private input roots as unset or explicit paths.
- `HardwareSettings`: optional GPU name, VRAM budget, safe model VRAM, and context token defaults.
- `ModelSpec`: model id, provider, role, relative model directory, enabled flag, quantization, context tokens, and root path.
- `ModelRegistry`: collection of `ModelSpec` values with directory status helpers.
- `ConfigBundle`: config directory plus app, paths, hardware, and registry.
- `DoctorCheck` / `DoctorResult`: non-sensitive local health check status and detail.

No database schema, API contract, embedding format, or source-data contract is introduced.

## Files to change

- `.agent/execplans/phase-0b-config-model-registry.md`
- `README.md`
- `.env.example`
- `src/private_memory_agent/config/__init__.py`
- `src/private_memory_agent/config/loader.py`
- `src/private_memory_agent/doctor.py`
- `src/private_memory_agent/cli.py`
- `src/private_memory_agent/models/__init__.py`
- `src/private_memory_agent/models/registry.py`
- `tests/test_config_loader.py`
- `tests/test_cli.py`
- `tests/test_model_registry.py`
- `tests/test_doctor.py`

## Implementation steps

1. Add model registry dataclasses that compute resolved directories and status without loading files.
2. Expand config dataclasses and loader construction to include hardware and registry objects.
3. Add serialization helpers for CLI-safe config and model output.
4. Implement `pma config show` and `pma models list`.
5. Expand `pma doctor` to include optional CUDA and `nvidia-smi` checks, model root checks, configured model directory checks, and data directory writability.
6. Add temp-directory based tests for config overrides, model directory validation, CLI commands, and doctor checks.
7. Run `pytest -q`.

## Tests and verification

Run:

- `pytest -q`
- `pma config show`
- `pma models list`
- `pma doctor`

Unit tests must use temporary config directories and model roots. They must not require GPU, actual local models, private data, Docker, network, or model runtime packages.

## Privacy and security

The registry only checks directory existence for explicitly configured model paths. It does not scan private source directories, enumerate model files, load weights, inspect embeddings, or contact network resources. Example source input roots remain unset. CLI output contains configuration and model registry status only, with no raw personal data.

## Performance and hardware

All checks are lightweight filesystem and environment checks. CUDA is detected through environment visibility and optional `nvidia-smi` availability only; absence of GPU tooling is a warning/info condition rather than a test failure. No VRAM is allocated.

## Rollback

Revert the Phase 0-B files and restore the Phase 0-A versions of the loader, CLI, doctor, README, and tests. Because this phase does not mutate data or model directories, rollback is limited to source code and docs.

## Open questions

None blocking for Phase 0-B.
