# ExecPlan: Phase 8-I Golden Retrieval Calibration Source Constraints

## Goal

Make golden questions useful for answer-quality evaluation by adding
source-coverage expectations, required/excluded/preferred source constraints,
privacy-safe diagnostics, and safer snippet inspection controls for
`pma eval golden`.

## Non-goals

- Do not change the default retrieval service behavior outside golden
  evaluation.
- Do not add new model calls, embeddings, rerankers, or retrieval algorithms.
- Do not inspect or commit local golden question content.
- Do not print raw private evidence, snippets, filenames, full paths, GPS,
  EXIF, OCR, LINE text, note bodies, captions, or raw model output by default.

## Current state

Phase 8-H added `pma eval golden` backed by the existing E2E retrieval and
leader flow. It supports local ignored `configs/golden_questions.local.yaml`,
safe example questions, retrieval-only/fake/real model modes, answer hiding,
optional `--show-answer`, optional `--show-snippets`, Markdown reports, JSON,
and JSONL. The current schema has question id, text, sources, and category.
Golden evaluation currently reports whether retrieval and answer generation
worked, but it does not validate whether returned evidence came from the
expected sources.

## Proposed design

Extend golden question records with:

- `expected_sources`
- `required_sources`
- `preferred_sources`
- `excluded_sources`
- `expected_keywords`
- `negative_keywords`
- `evaluation_focus`
- optional `source_policy`

Add CLI-level constraints:

- `--require-source`
- `--exclude-source`
- `--preferred-source`
- `--source-policy soft|strict`
- `--snippet-chars N`

Golden evaluation will compute effective constraints per question by combining
question config and CLI flags. It will derive the retrieval source filter from
explicit `sources`, expected/required/preferred sources, and exclusions. This
keeps constraints scoped to golden evaluation and avoids changing normal search
or E2E smoke.

Strict policy marks a question as failed if required sources are missing or
excluded sources appear. Soft policy records warnings/diagnostics but does not
fail solely on missing required or expected sources. Excluded sources are
filtered before retrieval in both policies unless explicitly included by future
features.

## Data contracts

Golden question YAML example:

```yaml
questions:
  - id: research_notes
    category: research
    text: "研究に関係するメモやLINEの記録を探してください。"
    expected_sources: [line, notes]
    required_sources: [notes]
    preferred_sources: [line]
    excluded_sources: [photos]
    expected_keywords: [研究]
    negative_keywords: []
    evaluation_focus: [evidence_relevance, source_coverage]
```

Per-result diagnostics:

- requested_sources
- expected_sources
- required_sources
- preferred_sources
- excluded_sources
- missing_expected_sources
- missing_required_sources
- excluded_source_violations
- source_policy
- retrieval_passed_source_policy
- expected_keywords_count
- negative_keywords_count

Markdown manual rating placeholders gain:

- source_policy_passed
- source_mismatch_notes
- irrelevant_evidence_notes

## Files to change

- `.agent/execplans/phase-8i-golden-retrieval-calibration-source-constraints.md`
- `configs/golden_questions.example.yaml`
- `src/private_memory_agent/evaluation/golden.py`
- `src/private_memory_agent/cli.py`
- `tests/test_golden_evaluation.py`
- `docs/RETRIEVAL.md`
- `docs/MODEL_RUNTIME.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Extend golden dataclasses and YAML parsing.
2. Add effective source constraint computation.
3. Pass constrained source filters into E2E smoke queries.
4. Add source-policy diagnostics and pass/fail logic to golden results.
5. Add CLI flags for source constraints and snippet character length.
6. Add source policy fields to JSON, Markdown, and JSONL reports.
7. Extend tests with synthetic data for parsing, strict/soft behavior,
   exclusions, balancing, snippet truncation, and privacy-safe output.
8. Update docs and overview.
9. Run tests, local smoke commands, and Git maintenance.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

If local DB exists:

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only --query-limit 5 --json
pma eval golden --config configs/paths.local.yaml --retrieval-only --query-id qst_preparation --require-source line --require-source notes --exclude-source photos --json
```

If local leader endpoint is running:

```bash
pma eval golden --config configs/paths.local.yaml --real-model --query-id qst_preparation --timeout-seconds 600 --max-tokens 512 --show-answer
```

Do not run large real-model evaluation by default.

## Privacy and security

Question text is still hidden from default output. Snippets remain hidden unless
`--show-snippets` is used, and then are truncated to `--snippet-chars` after
the existing redaction/path/GPS protections. Local configs and reports under
`data/local` remain ignored by Git.

## Performance and hardware

No new GPU requirement. Source constraints may reduce candidate volume for
golden evaluation. Real-model checks should stay single-question by default.

## Rollback

Revert the golden eval schema/CLI/report changes and docs. Existing Phase 8-H
golden evaluation and E2E smoke paths can continue without source-policy
diagnostics.

## Open questions

None blocking.
