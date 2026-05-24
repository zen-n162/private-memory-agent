# ExecPlan: Phase 8-H Golden Question Answer Quality Eval

## Goal

Add a repeatable golden-question evaluation workflow for Private Memory Agent so
real-model answers can be evaluated for usefulness, grounding, privacy safety,
and uncertainty handling without exposing private evidence by default.

## Non-goals

- Do not add new retrieval algorithms or ranking behavior.
- Do not improve model answer quality directly.
- Do not run large real-model batches by default.
- Do not print raw evidence snippets, LINE text, note bodies, captions,
  filenames, full paths, GPS, EXIF, OCR, or raw model output by default.
- Do not commit local golden question files.

## Current state

`pma e2e smoke` already performs retrieval, compact evidence packing,
fake/real leader answer generation, strict JSON extraction, retry diagnostics,
answer audit metrics, and explicit answer/snippet display controls. `pma eval
run` provides a synthetic deterministic evaluation harness, but there is no
local golden question set or report workflow for user-defined real-data quality
checks.

## Proposed design

Create a golden evaluation module that reuses `run_e2e_smoke` for the actual
retrieval and answer-generation path. Golden questions are loaded from
`configs/golden_questions.local.yaml` when present, otherwise from
`configs/golden_questions.example.yaml`. The local file is covered by the
existing `configs/*.local.yaml` ignore rule.

The new command is:

```bash
pma eval golden --config configs/paths.local.yaml
```

It supports retrieval-only, fake-model, real-model, query limiting, query id
selection, hidden-by-default answer display, Markdown output, JSON output, and
JSONL output.

## Data contracts

Golden question config:

```yaml
questions:
  - id: research_notes
    text: "研究に関係するメモを探してください。"
    sources: [line, notes]
    category: research
```

Golden result dimensions per question:

- retrieval_succeeded
- answer_succeeded
- evidence_count
- evidence_source_counts
- used_sources
- evidence_reference_count
- unknown_evidence_reference_count
- confidence
- unknowns_count
- json_retry_used
- json_retry_succeeded
- answer_validation_error
- privacy_safe_output

Manual rating placeholders:

- answer_correctness
- evidence_relevance
- source_coverage
- uncertainty_handling
- privacy_safety
- notes

## Files to change

- `configs/golden_questions.example.yaml`
- `src/private_memory_agent/evaluation/golden.py`
- `src/private_memory_agent/evaluation/__init__.py`
- `src/private_memory_agent/cli.py`
- `tests/test_golden_evaluation.py`
- `docs/ROADMAP.md`
- `docs/MODEL_RUNTIME.md`
- `docs/RETRIEVAL.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add the safe example golden question config.
2. Implement golden question loading, selection, report models, Markdown
   rendering, and JSONL rendering.
3. Add `pma eval golden` CLI options and output writing.
4. Add tests with synthetic DB data and fake or monkeypatched HTTP clients.
5. Update documentation and Japanese overview.
6. Run unit tests and overview validation.
7. Run small local retrieval-only and real-model checks if the local DB/server
   are available, without printing private evidence.
8. Commit and push privacy-safe changes according to Git maintenance rules.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

If local DB exists:

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only --query-limit 2 --json
```

If the leader endpoint is running:

```bash
pma eval golden --config configs/paths.local.yaml --real-model --query-limit 1 --timeout-seconds 600 --max-tokens 512 --json
```

Do not run large real-model evaluation by default.

## Privacy and security

Golden local questions may contain private facts, so default reports show
question ids and status only, not question text. Answer text is hidden unless
`--show-answer` is used. Snippets are hidden unless `--show-snippets` is used.
Markdown reports include a clear warning when answer/snippet display is enabled.
Raw model output is not included.

## Performance and hardware

No new model loading behavior is introduced. Real-model evaluation reuses the
existing local OpenAI-compatible leader endpoint and should start with
`--query-limit 1`, `--timeout-seconds 600`, and a small `--max-tokens` value.
Unit tests require no GPU, model server, network, or private data.

## Rollback

Remove `configs/golden_questions.example.yaml`, the golden evaluation module,
CLI subcommand, tests, and documentation updates. Existing E2E and synthetic
eval behavior will remain intact.

## Open questions

None blocking.
