# ExecPlan: Phase 0-D Japanese HTML Overview

## Goal

Create a Japanese, self-contained, human-readable HTML overview for Private
Memory Agent and add a maintenance rule so future Codex sessions update it when
the application changes.

## Non-goals

- Do not ingest, inspect, copy, or summarize private data.
- Do not run model inference or start model servers.
- Do not add external web assets, CDNs, fonts, images, or JavaScript.
- Do not claim unfinished real model integrations are complete.
- Do not add real LINE messages, note bodies, photo filenames, GPS, names,
  secrets, environment values, or private directory listings.

## Current state

The repository has Markdown docs for architecture, security/privacy, roadmap,
retrieval, data model, evaluation, API, and model runtime. It has no
`docs/overview_ja.html` and no `.agent/HTML_OVERVIEW_MAINTENANCE.md`.

The app currently includes CLI, configuration, local SQLite metadata storage,
photo/LINE/notes ingestion, text search, embeddings interfaces and optional real
embedding adapter, model runtime clients, photo/text annotation flows, retrieval,
leader query flow, privacy/critic guardrails, timeline/entity services, local
FastAPI API/UI, synthetic evaluation, and runtime planning. Some real local
model usage is supported through OpenAI-compatible endpoints, but full model
orchestration and all production-grade integrations remain limited.

## Proposed design

Add `docs/overview_ja.html` as a single static Japanese HTML page with inline
CSS and no JavaScript. It will explain the application for non-specialists while
including enough implementation detail for developers. It will include CSS-only
architecture and processing-flow diagrams.

Add `.agent/HTML_OVERVIEW_MAINTENANCE.md` with update triggers, no-update cases,
required sections, privacy rules, status-discipline rules, verification, and
done criteria.

Update `AGENTS.md` to reference the maintenance guide concisely. Update
`README.md` with a brief link to the overview. Do not create `docs/README.md`
because it is absent and not needed for this phase.

Optionally add `scripts/check_overview_html.py` using only the Python standard
library to validate the static page.

## Data contracts

Static page requirements:

- `docs/overview_ja.html`
- `<html lang="ja">`
- inline CSS only
- no external assets or scripts
- required Japanese sections listed in the user request

Validation script checks:

- file exists
- lang is Japanese
- key section labels are present
- no `TODO`
- no obvious CDN references

## Files to change

- `.agent/execplans/phase-0d-html-overview.md`
- `.agent/HTML_OVERVIEW_MAINTENANCE.md`
- `docs/overview_ja.html`
- `scripts/check_overview_html.py`
- `AGENTS.md`
- `README.md`

## Implementation steps

1. Create the ExecPlan.
2. Write the self-contained Japanese HTML overview.
3. Write the maintenance guide.
4. Add a concise AGENTS.md reference to the guide.
5. Add a brief README.md link.
6. Add optional HTML validation script.
7. Run `python scripts/check_overview_html.py`.
8. Run `pytest -q`.
9. Run `pma --help`.

## Tests and verification

- `python scripts/check_overview_html.py`
- `pytest -q`
- `pma --help`

These checks must not require GPU, model files, private data, model servers,
Docker, or network.

## Privacy and security

The HTML uses only fictional examples. It does not include real private paths,
names, LINE text, note bodies, photo filenames, GPS, secrets, directory
listings, or environment values. The validation script does not read private
data.

## Performance and hardware

This is static documentation and a lightweight stdlib validation script. It has
no GPU, VRAM, model, or network requirements.

## Rollback

Remove the new HTML page, maintenance guide, validation script, and README /
AGENTS references. Existing application behavior remains unchanged.

## Open questions

None blocking.
