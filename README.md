# Private Memory Agent

Private Memory Agent is a local-first skeleton for a personal memory assistant. The intended product will answer questions from local evidence such as photos, LINE exports, notes, and timelines, while preserving uncertainty and showing the sources behind answers.

For a Japanese human-readable overview of the application, architecture, privacy
policy, and roadmap, open [`docs/overview_ja.html`](docs/overview_ja.html).

## Local-First Principles

- Private source data stays on this machine.
- Example configs do not contain real photos, notes, chats, GPS data, embeddings, or databases.
- Model files are not copied, moved, downloaded, or inspected by this phase.
- Network access and private-data logging default to disabled.
- Source-data paths remain unset until a later explicit ingestion phase.

## Quick Start

Use Python 3.11 or newer.

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
pma --help
pma doctor
pma config show
pma models list
```

`pma doctor` verifies Python version, optional CUDA and `nvidia-smi` visibility, example config presence, privacy defaults, model root existence, configured model directory status, and app data directory writability without touching model weights or private source data.

## Configuration

Example configuration lives in `configs/`:

- `app.example.yaml`: local privacy defaults.
- `paths.example.yaml`: model root and unset future input roots.
- `models.example.yaml`: model role examples for later phases.

The model root is configurable and defaults to:

```text
/home/zennakamura/MyApplication/models
```

Local overrides may be supplied through environment variables such as `PMA_MODEL_ROOT`, `PMA_ENV`, `PMA_ALLOW_NETWORK`, and `PMA_LOG_PRIVATE_DATA`.

Per-model overrides are also supported for configured model ids:

```bash
PMA_MODEL_LEADER_ENABLED=false
PMA_MODEL_LEADER_DIR=alternate/leader-model
```

## Development

The project uses a `src/` layout and exposes the CLI entry point `pma`.

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
```

Use `python -m pytest` so the test runner comes from the active Python
environment. Ruff configuration is included in `pyproject.toml`.
