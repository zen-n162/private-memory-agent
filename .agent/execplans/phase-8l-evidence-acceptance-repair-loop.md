# ExecPlan: Phase 8-L Evidence Acceptance Repair Loop

## Goal

Make golden evaluation distinguish candidate retrieval from usable evidence. When
leader-guided relevance judging says every candidate is generic or weak, the
report should no longer treat that as high relevance or a clean retrieval
success for answer-quality purposes. Add explicit relevance policy controls and
repair diagnostics.

## Non-goals

- Do not add new ingestion, annotation, model serving, or vector-store features.
- Do not hard-code QST- or interview-specific retrieval behavior.
- Do not print private question text, snippets, filenames, paths, LINE text,
  note bodies, captions, OCR, GPS, or raw model output by default.
- Do not require real model servers, GPUs, network, or private data in unit
  tests.

## Current state

Phase 8-K added optional leader-guided retrieval planning, deterministic
plan-aware evidence judging, leader reranking, and one-step retrieval repair.
However, golden results still use `retrieval_succeeded` and a keyword/source
weighted `relevance_score` as if candidate evidence were usable evidence. A
local qst_preparation run can return five generic candidates with
`should_use=false` for all, while `retrieval_succeeded=true`,
`passed=true`, and `relevance_score=1.0`.

## Proposed design

Keep backward-compatible fields, but add explicit evidence acceptance fields:

- `candidate_retrieval_succeeded`
- `usable_evidence_succeeded`
- `usable_evidence_count`
- `unusable_evidence_count`
- `should_use_evidence_count`
- `minimum_relevance_threshold`
- `relevance_policy`
- `relevance_policy_passed`
- `source_coverage_score`
- `keyword_relevance_score`
- `plan_relevance_score`
- `final_relevance_score`
- repair diagnostics for attempted/improved/pre/post usable evidence counts

`retrieval_succeeded` remains candidate-oriented for compatibility. New strict
relevance policy can fail a result when no usable evidence remains after repair.
Soft policy keeps the command usable but exposes warnings and low final
relevance.

## Data contracts

Golden JSON and Markdown reports gain privacy-safe scalar/counter fields only.
No raw evidence or plan text is displayed unless existing explicit options such
as `--show-plan`, `--show-relevance`, or `--show-snippets` are used.

CLI additions:

- `--minimum-relevance-score FLOAT`
- `--require-usable-evidence`
- `--relevance-policy soft|strict`

## Files to change

- `src/private_memory_agent/evaluation/golden.py`
- `src/private_memory_agent/cli.py`
- `tests/test_golden_evaluation.py`
- `docs/RETRIEVAL.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Extend golden evaluation options, result schema, JSON output, Markdown
   output, and manual rating placeholders.
2. Add deterministic score helpers for source coverage, keyword relevance, plan
   relevance, final relevance, and usable-evidence policy.
3. Replace repair count-only tracking with privacy-safe repair diagnostics.
4. Ensure one-step repair uses plan-specific/main-entity query expansion and
   reports whether usable evidence improved.
5. Add CLI flags and wire them into `GoldenEvalOptions`.
6. Add synthetic tests for generic-only evidence, strict/soft policy, repair
   improvement, repair non-improvement, and privacy-safe output.
7. Update retrieval/roadmap/overview docs.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

If local DB exists, run:

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only \
  --query-id qst_preparation \
  --leader-plan \
  --leader-rerank \
  --retrieval-repair 1 \
  --show-relevance \
  --json

pma eval golden --config configs/paths.local.yaml --retrieval-only \
  --query-id qst_preparation \
  --leader-plan \
  --leader-rerank \
  --retrieval-repair 1 \
  --minimum-relevance-score 0.6 \
  --require-usable-evidence \
  --relevance-policy strict \
  --json
```

## Privacy and security

All new diagnostics are counters, source labels, booleans, and privacy-safe
evidence IDs. Raw evidence, private questions, repair query text, model output,
and snippets remain hidden by default.

## Performance and hardware

No GPU or model server is required for unit tests. Leader planning remains
optional and locally configured; this phase adds only deterministic report
logic around existing retrieval/evaluation paths.

## Rollback

Revert this phase's edits to the listed files. Existing Phase 8-K golden
evaluation behavior will remain available without the acceptance/relevance
policy fields.

## Open questions

None blocking.
