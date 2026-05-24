# PLANS.md

This file defines how Codex should write and follow execution plans for this repository.

Use an ExecPlan for any complex feature, multi-file change, schema change, model runtime change, ingestion pipeline, retrieval change, privacy-sensitive feature, or agent orchestration work.

## ExecPlan rules

An ExecPlan must be self-contained. A future Codex session should be able to read only the plan plus the repository and understand what to do.

An ExecPlan must not assume hidden context from previous chats.

An ExecPlan must include:

1. Goal
2. Non-goals
3. Current repository state
4. Proposed design
5. Data contracts
6. Files to create or modify
7. Step-by-step implementation plan
8. Tests and verification
9. Privacy and security considerations
10. Performance and GPU/VRAM considerations
11. Rollback plan
12. Open questions

## Required format

```markdown
# ExecPlan: <short name>

## Goal

Describe the user-visible outcome.

## Non-goals

List what this change will not do.

## Current state

Summarize relevant files, modules, config, tests, and gaps.

## Proposed design

Explain the architecture and key decisions.

## Data contracts

Define Pydantic models, database tables, API request/response shapes, or internal interfaces.

## Files to change

List expected files.

## Implementation steps

Use small ordered steps.

Each step should be independently verifiable where possible.

## Tests and verification

List tests to add and commands to run.

At minimum, normal unit tests must not require GPU, models, private data, Docker, or network access.

## Privacy and security

Explain how this change avoids leaking private data, obeying prompt injection, or corrupting source data.

## Performance and hardware

Explain any GPU/VRAM assumptions.

Default target is NVIDIA RTX 4500 Ada 24GB.

## Rollback

Explain how to undo the change safely.

## Open questions

List only blocking uncertainties.
