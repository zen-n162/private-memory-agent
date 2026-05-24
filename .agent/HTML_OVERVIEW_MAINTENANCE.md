# HTML Overview Maintenance

## Purpose

`docs/overview_ja.html` is the Japanese, human-readable overview of Private
Memory Agent. It explains the application for non-specialists while preserving
enough architecture and operation detail for developers.

The file is intentionally self-contained:

- static HTML
- inline CSS
- no JavaScript requirement
- no external CDN, images, fonts, or network dependency

## When Codex Must Update It

Update `docs/overview_ja.html` whenever a change affects user-visible behavior,
architecture, privacy/security policy, model runtime support, CLI commands,
data sources, retrieval/agent flow, evaluation, API/UI behavior, or roadmap
status.

Examples:

- new ingestion source or storage table
- new or changed CLI command
- new API or UI capability
- new model role, runtime adapter, or serving profile
- changed privacy redaction or evidence policy
- changed roadmap phase status
- changed local setup or verification workflow

## When It Does Not Need An Update

No update is required for changes that do not affect the overview, such as:

- internal refactors with no behavior or architecture change
- typo fixes in unrelated code comments
- test-only changes that do not alter supported workflows
- formatting-only edits outside documentation

If unsure, prefer a small conservative update.

## Required Sections

The overview must keep these sections:

- Title: Private Memory Agent
- One-sentence summary
- What this app can do
- Data sources
- AI agent architecture
- Processing flow
- Model roles
- Evidence-grounded answer policy
- Privacy and security policy
- Fictional example answer
- Development roadmap
- Developer commands
- Limitations
- Update history

## Privacy Rules

Never include:

- real LINE messages
- real note bodies
- real photo filenames
- real GPS coordinates
- real personal names
- private source directory listings
- secrets, tokens, or environment values
- copied private metadata

Examples must be clearly fictional.

## Style Rules

- Write in Japanese.
- Keep the page readable for non-specialists.
- Include developer-level detail where useful.
- Use semantic headings.
- Keep CSS inline.
- Do not use external assets, CDNs, web fonts, or JavaScript dependencies.
- Keep contrast accessible.
- Avoid heavy decoration and animations.

## Implementation Status Discipline

Do not mark unfinished features as complete. Use conservative labels:

- `基本実装済み` for repository-backed basic functionality.
- `限定実装` when the code has a usable adapter but real operation depends on
  local servers, optional packages, or manual setup.
- `実装中` for partial work.
- `計画中` when the feature is not implemented.

Do not claim all real model integrations are complete unless the repository
code and tests support that claim.

## Verification Commands

Run these after editing the overview:

```bash
python scripts/check_overview_html.py
pytest -q
pma --help
```

If a command cannot be run in the current environment, record the reason in the
final response.

## Done Criteria

The update is done when:

- `docs/overview_ja.html` remains self-contained.
- Required sections are present.
- No private data or external asset dependency is introduced.
- Status labels match the current repository conservatively.
- `AGENTS.md` still points future Codex sessions to this maintenance guide.
- Verification commands have been run or skipped with a clear reason.
