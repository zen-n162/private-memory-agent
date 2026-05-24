# ExecPlan: Local Raw Data Paths

## Goal

Add configuration support for real local raw data roots through an ignored `configs/paths.local.yaml` file, then let `pma doctor --config configs/paths.local.yaml` check the configured source roots without ingesting data, printing filenames, or logging private content.

## Non-goals

- Do not ingest photos, LINE exports, notes, OCR, GPS, metadata, or any private payload.
- Do not copy, move, rename, delete, hash, enumerate, or index source files.
- Do not implement model calls, vector DB, storage schemas, or ingestion pipelines.
- Do not commit real local paths, local configs, `.env.local`, or `.env.private`.
- Do not add tests that require the real local source directories.

## Current state

The repository has a Python CLI with `pma doctor`, `pma config show`, and `pma models list`. The config loader reads example YAML files from `configs/` and supports model/path settings. `configs/paths.local.yaml` exists locally and is ignored by git. `.gitignore` already includes local config and environment patterns, but they should be verified and preserved. `configs/paths.example.yaml` currently includes placeholder raw source paths, which should be made generic and portable.

## Proposed design

Extend the config layer with typed raw source settings:

- `RawSourceSettings`: source id, kind, enabled, optional path, recursive flag, read-only intent.
- `PathSettings.raw_sources`: mapping of configured source categories.

The loader will read `configs/paths.example.yaml` as the portable baseline and optionally overlay a local paths config file. CLI commands will accept `--config <path>` as an alias for a paths overlay file. The local overlay file may contain real source roots, but application logic will never hard-code those paths.

`pma doctor --config configs/paths.local.yaml` will add raw source checks for each configured source:

- configured or not configured
- exists or missing
- readable or not readable
- file or directory
- whether it appears read-only from the app's perspective

Doctor output will identify only source categories such as `photos`, `line`, and `notes`; it will not print filenames, LINE text, note bodies, OCR text, image metadata, GPS, personal names, or directory listings.

## Data contracts

- `RawSourceSettings`
  - `source_id: str`
  - `kind: str`
  - `enabled: bool`
  - `path: Path | None`
  - `recursive: bool`
  - `read_only: bool`
- `PathSettings`
  - existing model/data paths
  - `input_roots` for backward compatibility
  - `raw_sources: dict[str, RawSourceSettings]`
- `ConfigBundle`
  - records `paths_config_path` when a local overlay file is supplied.
- `DoctorCheck`
  - non-sensitive category-level source checks.

No database, API, vector, model, or ingestion data contract is introduced.

## Files to change

- `.agent/execplans/local-raw-data-paths.md`
- `.gitignore`
- `configs/paths.example.yaml`
- `configs/paths.local.yaml` ignored local file
- `src/private_memory_agent/config/__init__.py`
- `src/private_memory_agent/config/loader.py`
- `src/private_memory_agent/doctor.py`
- `src/private_memory_agent/cli.py`
- `tests/conftest.py`
- `tests/test_config_loader.py`
- `tests/test_cli.py`
- `tests/test_doctor.py`

## Implementation steps

1. Add typed raw source settings and local paths overlay support to the config loader.
2. Add `--config` support for `pma doctor` and other config-loading commands while retaining `--config-dir`.
3. Update doctor to emit category-level raw source checks without listing contents or path names by default.
4. Make `configs/paths.example.yaml` portable by leaving raw source paths unset and disabled.
5. Create or update ignored `configs/paths.local.yaml` with the real local paths supplied by the user.
6. Add temp-directory tests for local path overlay loading and doctor checks.
7. Run `pytest -q`.
8. Run `pma doctor --config configs/paths.local.yaml` if the command is available.

## Tests and verification

Run:

- `pytest -q`
- `pma doctor --config configs/paths.local.yaml`

Unit tests must use `tmp_path` fake source roots and must not require the real local source paths, GPU, network, model files, or private data.

## Privacy and security

The source roots are treated as read-only. Doctor checks use `Path.exists`, `Path.is_dir`, `Path.is_file`, and `os.access` only. The app does not enumerate directory contents, open files, copy files, or print private filenames or payloads. Local configs and private env files remain ignored by git.

## Performance and hardware

The checks are lightweight path metadata checks. They do not use GPU, load models, allocate VRAM, or perform filesystem scans.

## Rollback

Revert the config loader, CLI, doctor, tests, and portable example config changes. Remove the ignored local config if desired. No source data or model data is modified by this plan.

## Open questions

None blocking.
