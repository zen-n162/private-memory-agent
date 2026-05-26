# Phase 10-A: Autonomous Capability Planner and Executor

## Goal

Add an autonomous planning layer where DeepSeek Leader, or a deterministic
fallback in tests/offline mode, selects and composes registered capabilities
instead of relying on one route per query pattern.

The initial implementation should preserve the working temporal, visual, and
generic chat paths while exposing a structured `TaskPlan`, executed
capabilities, observations, and evidence sufficiency in the API and UI.

## Plan

1. Add a privacy-safe `CapabilityRegistry` with metadata for existing local
   capabilities such as date parsing, photo annotation search, LINE/notes
   search, semantic search, reranking, evidence judging, clustering, answer
   synthesis, privacy filtering, and UI render targets.
2. Add validated `TaskPlan`, `TaskPlanStep`, `Observation`, and execution
   result models.
3. Add a deterministic fallback planner and a pluggable Leader planner
   interface. The fallback should create generic capability plans for temporal,
   visual, text, and hybrid questions without hard-coding one private topic.
4. Add a `CapabilityExecutor` that validates steps, enforces budgets, emits
   trace-friendly observations, and handles missing/invalid capabilities
   safely.
5. Integrate the autonomous plan metadata into the chat console response for
   retrieval-only, fake-model, and real-model modes without replacing the
   already working evidence builders.
6. Add response schema fields for `task_plan`, `selected_capabilities`,
   `executed_steps`, `observations`, and `replans`.
7. Add an `/ui` "Autonomous Plan" panel showing selected capabilities,
   execution steps, observations, replans, and evidence sufficiency with safe
   summaries only.
8. Add synthetic tests for registry metadata, plan validation, execution,
   replan behavior, API payload fields, UI rendering, and representative
   temporal/visual/text questions.
9. Update docs and `docs/overview_ja.html`.

## Privacy

Capability plans and observations must not include raw LINE text, note bodies,
photo filenames, full paths, GPS, EXIF, OCR dumps, raw prompts, raw model
outputs, or chain-of-thought. They may expose capability names, source labels,
counts, safe summaries, statuses, and evidence IDs already allowed by the chat
contract.

## Verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

Manual local checks after restarting the API server:

```text
ラーメンが写っている写真はどれ？
ラーメンを食べに行っているのはいつ？
2025年12月で、ご飯を食べに行っているのはいつ？
```

Expected: the UI shows an Autonomous Plan with selected capabilities, the
existing Matching Photos / Candidate Dates layouts continue to work, and all
outputs remain privacy-safe by default.
