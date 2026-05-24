# AGENTS.md

## Project identity

This repository implements **Private Memory Agent**, a local-first multimodal personal memory application.

The application answers questions about the user's own local data by combining:

- Photo folders
- LINE chat exports
- Notes app exports
- Local multimodal and text models
- Evidence-grounded retrieval
- Timeline reconstruction
- Privacy-preserving reasoning

The product must never behave like a generic chatbot that guesses. It must answer only from available local evidence, explicitly state uncertainty, and show the sources used.

## Repository root

Expected root:

```text
~/MyApplication/private-memory-agent
```

## Japanese HTML overview

Maintain `docs/overview_ja.html` according to `.agent/HTML_OVERVIEW_MAINTENANCE.md`.
When user-visible behavior, architecture, privacy policy, model/runtime support,
CLI commands, API/UI behavior, or roadmap status changes, update the HTML
overview in the same change.

## Git update maintenance

After application updates, follow `.agent/GIT_UPDATE_MAINTENANCE.md` before
committing or pushing changes to GitHub.

## Local user data roots

The user's real private data roots are configured through `configs/paths.local.yaml`.

Do not hard-code real private data paths in application code.

The expected source categories are:

- photos
- LINE chat exports
- notes exports

The local config may point to directories outside this repository.

Rules:

- Treat all configured source paths as read-only.
- Never copy real data into `tests/fixtures`.
- Never commit local config files.
- Never print real filenames, LINE text, note bodies, OCR text, GPS coordinates, or personal names in normal logs.
- `pytest` must not require real data paths.
- Real data checks must be explicit manual or smoke commands, not default tests.
