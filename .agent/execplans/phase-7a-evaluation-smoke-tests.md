# ExecPlan: Phase 7-A Evaluation And Smoke Tests

## Goal

Create a repeatable synthetic evaluation harness that checks local retrieval,
grounding shape, insufficient-evidence handling, and privacy redaction without
using real personal data, GPU, model files, model servers, Docker, or network
access.

## Non-goals

- Do not evaluate real private data.
- Do not load real embedding, chat, vision, or reranker models.
- Do not call external APIs.
- Do not implement LLM-judged evaluation.
- Do not replace the existing unit tests.

## Current state

The project already has:

- SQLite storage and repositories.
- Retrieval over LINE, notes, and photo annotations.
- A minimal query flow using `LeaderAgent`.
- A deterministic `FakeLeaderChatModelClient`.
- CLI query and API commands.
- A placeholder `scripts/smoke_test.py` that only prints success.

There is no repeatable quality/safety eval harness or `pma eval run` command.

## Proposed design

Add `private_memory_agent.evaluation` with:

- Synthetic dataset generation into a temporary or explicit SQLite database.
- A fixed set of eval cases covering date, person uncertainty, place,
  insufficient evidence, prompt injection, LINE joke-vs-fact, and privacy
  redaction scenarios.
- A deterministic runner that calls the existing `run_query_flow` with the fake
  leader client.
- Simple rule-based metrics:
  - `evidence_recall_proxy`
  - `groundedness_check`
  - `privacy_leak_check`
  - `insufficient_evidence_handling`

Add `pma eval run`, which generates synthetic data by default in a temporary DB
and prints JSON. The command can optionally use an explicit DB path for local
debugging.

Replace `scripts/smoke_test.py` with a real local smoke script that imports the
package from `src`, runs the synthetic eval, asserts all default metrics pass,
and prints a count-only summary.

## Data contracts

Data classes:

- `SyntheticEvalData`
  - `db_path`
  - `evidence_ids_by_key`
  - `private_markers`
- `EvalCase`
  - `case_id`
  - `category`
  - `question`
  - `expected_evidence_ids`
  - `sources`
  - `expect_insufficient`
  - `private_markers`
- `EvalCaseResult`
  - case metadata
  - answer/evidence ids/sources
  - per-case metric booleans
- `EvalRunResult`
  - aggregate metrics
  - case results
  - pass/fail summary

CLI:

```bash
pma eval run
pma eval run --db /tmp/pma-eval.sqlite3
```

## Files to change

- `.agent/execplans/phase-7a-evaluation-smoke-tests.md`
- `src/private_memory_agent/evaluation/__init__.py`
- `src/private_memory_agent/evaluation/harness.py`
- `src/private_memory_agent/cli.py`
- `scripts/smoke_test.py`
- `tests/test_evaluation.py`
- `docs/EVALUATION.md`
- `docs/ARCHITECTURE.md`

## Implementation steps

1. Add synthetic eval data generator and fixed eval case definitions.
2. Add eval runner using existing query flow and fake leader client.
3. Add aggregate metrics and JSON serialization helpers.
4. Add `pma eval run` CLI command.
5. Replace the placeholder smoke script with a real synthetic eval smoke.
6. Add deterministic unit tests for dataset generation, metrics, CLI output,
   and privacy redaction.
7. Document eval usage.
8. Run `pytest -q` and `python scripts/smoke_test.py`.

## Tests and verification

- `pytest -q`
- `python scripts/smoke_test.py`
- `pma eval run`

Default tests and smoke runs must not require GPU, model files, model servers,
network, Docker, or private source data.

## Privacy and security

All eval data is synthetic and generated under a temporary or caller-provided DB
path. Eval output is redacted by default and includes count-only summaries plus
synthetic evidence ids. Privacy checks scan serialized results for known
synthetic private markers.

Prompt-injection cases put adversarial text inside note bodies and assert that
the fake query flow does not surface obeyed injected instructions.

## Performance and hardware

The default eval is CPU-only and uses small SQLite data. It does not require the
RTX 4500 Ada GPU or any VRAM.

## Rollback

Remove the evaluation package, CLI command, tests, smoke script changes, and
docs. Existing ingestion, retrieval, query, API, and UI behavior remains
unchanged.

## Open questions

None blocking. Future phases can add real-model eval modes behind explicit
opt-in flags.
