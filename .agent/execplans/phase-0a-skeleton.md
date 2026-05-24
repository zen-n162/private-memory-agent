# ExecPlan: Phase 0-A Repository Skeleton

## Goal

Create a clean Python 3.11 project skeleton for Private Memory Agent with a local-first command line entry point, basic configuration loading, example configuration files, development tooling configuration, and minimal tests that run without GPU, models, network, or private data.

## Non-goals

- Do not implement ingestion for photos, LINE exports, notes, GPS, or other private sources.
- Do not load, copy, move, download, or inspect model files.
- Do not implement retrieval, database schemas, API servers, UI, or agent orchestration.
- Do not add real personal data, private paths to source data, embeddings, caches, or local runtime outputs.

## Current state

The repository already contains placeholder files for `pyproject.toml`, `README.md`, `src/private_memory_agent/cli.py`, `configs/app.example.yaml`, and `configs/paths.example.yaml`, but several are empty. `configs/models.example.yaml` contains a model-root default and model role examples. `.env.example` exists, but `.gitignore` currently ignores `.env.*`, which also ignores `.env.example`. There is no package initialization, no config loader module, no tests directory, and no installed CLI entry point yet.

## Proposed design

Use a standard `src/` Python package layout with `private_memory_agent` as the package. Define a console script named `pma` in `pyproject.toml` that calls `private_memory_agent.cli:main`. Keep runtime dependencies empty for Phase 0-A by using the Python standard library for CLI and config loading. Add a conservative YAML subset parser that can read the repository's simple example YAML files without requiring network-installed packages.

The CLI will expose `pma doctor` as a local-only placeholder health check. It will verify Python version, check example config files, load privacy defaults, confirm the configured model root, and explicitly report that ingestion, model runtime, and API serving are not implemented in this phase.

## Data contracts

- `AppSettings`: app name, environment, privacy mode, network flag, private-data logging flag, and default timezone.
- `PathSettings`: configurable model root, local app data directory, and optional input root placeholders for future private sources.
- `ConfigBundle`: resolved config directory plus app, paths, and raw model mapping.
- `DoctorResult`: lists non-sensitive checks and returns success only when required skeleton checks pass.

No database tables, API request/response schemas, model runtime contracts, or source-data contracts are introduced.

## Files to change

- `pyproject.toml`
- `README.md`
- `.gitignore`
- `.env.example`
- `configs/app.example.yaml`
- `configs/paths.example.yaml`
- `src/private_memory_agent/__init__.py`
- `src/private_memory_agent/cli.py`
- `src/private_memory_agent/config/__init__.py`
- `src/private_memory_agent/config/loader.py`
- `src/private_memory_agent/doctor.py`
- `tests/test_config_loader.py`
- `tests/test_cli.py`

## Implementation steps

1. Fill `pyproject.toml` with Python 3.11 metadata, package discovery, the `pma` console script, pytest settings, and ruff settings.
2. Add package initialization and a standard-library config loader that reads example YAML files and environment overrides.
3. Add `pma doctor` with placeholder checks that avoid GPU, model, network, and private data access.
4. Fill example app and path configs while leaving source input roots unset.
5. Update `.env.example` and `.gitignore` so example environment settings can be tracked without committing local secrets.
6. Add README content describing the local-first scope and Phase 0-A limitations.
7. Add unit tests for config defaults, environment overrides, and the CLI doctor command.

## Tests and verification

Run:

- `pytest -q`
- `pma --help`
- `pma doctor`

The tests must not require GPU, models, private data, Docker, network access, or installed model runtimes.

## Privacy and security

All added files will contain examples only. Source data paths remain `null` placeholders. The config loader will not enumerate private folders or model directories. The doctor output will avoid reading private files and will only report skeleton-level checks. No external APIs, uploads, public sharing, or model downloads are introduced.

## Performance and hardware

Phase 0-A performs no model inference and has no GPU/VRAM dependency. The model root remains configurable with the default `/home/zennakamura/MyApplication/models`, and GPU assumptions are limited to documentation/config placeholders for later phases.

## Rollback

Because this phase only adds skeleton files and fills empty placeholders, rollback is safe by removing the added package modules, tests, and ExecPlan, then restoring the touched placeholder files from git or deleting the untracked skeleton files.

## Open questions

None blocking for Phase 0-A.
