# Git Update Maintenance

## Purpose

This file defines the repository maintenance rule for Private Memory Agent:
after an application update is implemented and verified, Codex should prepare a
Git update for the configured GitHub repository.

Repository:

```text
https://github.com/zen-n162/private-memory-agent.git
```

## When To Update Git

After every application update, bug fix, CLI change, runtime change, ingestion
change, retrieval change, documentation update, or privacy/security rule change:

1. Review the working tree.
2. Confirm no private local data or ignored local config is staged.
3. Run the relevant verification commands.
4. Commit the intended changes with a concise message.
5. Push to the configured GitHub remote when requested or when the user has
   asked Codex to keep Git updated.

## Privacy Rules

Never stage or commit:

- `configs/*.local.yaml`
- `.env.local`
- `.env.private`
- local SQLite databases
- raw photos
- LINE exports
- note bodies or exports
- GPS/EXIF/OCR/private metadata dumps
- embeddings or vector-store data
- model files
- cache or log directories

Prefer explicit `git add <path>` commands over broad staging when the working
tree contains scratch files or local-only artifacts.

## Verification

Before committing application updates, run the most relevant checks, normally:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

For runtime-only or documentation-only updates, record any skipped checks and
why in the final response.

## Done Criteria

- The remote is configured for `zen-n162/private-memory-agent.git`.
- Only intended, privacy-safe files are staged.
- A concise commit exists.
- The branch is pushed to the GitHub remote when requested.
- The final response summarizes changed files, verification, commit, and push
  status.
