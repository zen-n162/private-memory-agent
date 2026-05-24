# ExecPlan: Phase 8-K Leader-Guided Intelligent Retrieval Planning

## Goal

Add an optional model-assisted retrieval planning layer so Private Memory Agent
can understand a user question, derive source preferences and retrieval
queries, score candidate evidence relevance, and optionally repair weak
retrieval results. The feature should improve golden evaluation beyond
hand-written keywords while keeping source constraints and keyword diagnostics
as deterministic guardrails.

## Non-goals

- Do not hard-code QST, interview, or any user-specific topic logic.
- Do not remove existing source constraints, keyword diagnostics, or fake-model
  evaluation paths.
- Do not print raw private questions, LINE text, note bodies, captions,
  filenames, paths, GPS, OCR, raw model output, or full retrieval plans by
  default.
- Do not require a real DeepSeek server, GPU, network, or private data in unit
  tests.
- Do not enable real model evidence judging by default.

## Current state

- `pma eval golden` supports source constraints, keyword calibration, fake
  model, real model, Markdown/JSONL reports, and privacy-safe output.
- `RetrievalService` supports source filters, FTS/LIKE search, media annotation
  search, optional embeddings, source balancing, and keyword boost/negative
  penalties.
- `LeaderAgent` and `OpenAICompatibleHTTPClient` already support local
  OpenAI-compatible leader endpoints and robust answer JSON extraction.
- The current golden retrieval calibration is deterministic and keyword driven.

## Proposed design

- Add `private_memory_agent.agent.retrieval_planner` with:
  - `RetrievalPlan`
  - `RetrievalPlanMetadata`
  - `RetrievalPlanner` protocol
  - `FakeRetrievalPlanner`
  - `DeterministicRuleBasedRetrievalPlanner`
  - `LeaderRetrievalPlanner`
  - `EvidenceRelevanceJudge` protocol
  - `DeterministicEvidenceRelevanceJudge`
  - `FakeEvidenceRelevanceJudge`
- Golden evaluation gets optional flags:
  - `--leader-plan`
  - `--leader-rerank` / `--leader-judge-evidence`
  - `--retrieval-repair N`
  - `--show-plan`
  - `--show-relevance`
- In golden evaluation, a plan is generated before E2E retrieval. The original
  question remains the leader-answer question, while plan-derived retrieval
  text and concepts are used only for retrieval.
- Deterministic source constraints still override unsafe broadening. Excluded
  sources remain excluded. Required sources remain required.
- Relevance judging runs on retrieved candidates and returns privacy-safe
  aggregate metadata. In this phase the default judge is deterministic; real
  leader judging remains opt-in architecture, not default behavior.
- Retrieval repair can add one or more additional plan queries when first-pass
  relevance is weak.

## Data contracts

`RetrievalPlan`:

- `intent: str`
- `main_entities: tuple[str, ...]`
- `specific_concepts: tuple[str, ...]`
- `generic_concepts: tuple[str, ...]`
- `temporal_hints: tuple[str, ...]`
- `source_preferences: tuple[str, ...]`
- `source_constraints: tuple[str, ...]`
- `retrieval_queries: tuple[str, ...]`
- `excluded_concepts: tuple[str, ...]`
- `evidence_acceptance_criteria: tuple[str, ...]`
- `uncertainty_notes: tuple[str, ...]`

`EvidenceRelevanceScore`:

- `evidence_id: str`
- `relevance_score: float`
- `specificity: specific | generic | weak | unrelated`
- `should_use: bool`
- `reason_category: str`
- `matched_plan_concepts: tuple[str, ...]`

Golden result metadata adds plan/relevance counters but hides full plan text
unless `--show-plan` is explicit.

## Files to change

- `src/private_memory_agent/agent/retrieval_planner.py`
- `src/private_memory_agent/agent/__init__.py`
- `src/private_memory_agent/evaluation/golden.py`
- `src/private_memory_agent/e2e.py`
- `src/private_memory_agent/cli.py`
- `tests/test_retrieval_planner.py`
- `tests/test_golden_evaluation.py`
- `docs/RETRIEVAL.md`
- `docs/MODEL_RUNTIME.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add retrieval plan and evidence relevance schemas with validation.
2. Add fake, deterministic, and leader-backed planner implementations.
3. Add deterministic relevance judge and ranking helpers.
4. Extend golden evaluation options and CLI flags.
5. Use plan-derived retrieval text/concepts in golden E2E queries.
6. Add optional deterministic leader-rerank/judge metadata and simple repair
   loop for weak evidence.
7. Keep default output privacy-safe; reveal plan/relevance details only through
   explicit flags.
8. Add synthetic tests for schema, planning, reranking, repair, constraints,
   and privacy.
9. Update docs and Japanese overview.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

If the local leader server is running, run:

```bash
pma eval golden \
  --config configs/paths.local.yaml \
  --retrieval-only \
  --query-id qst_preparation \
  --leader-plan \
  --json
```

Then locally only:

```bash
pma eval golden \
  --config configs/paths.local.yaml \
  --retrieval-only \
  --query-id qst_preparation \
  --leader-plan \
  --leader-rerank \
  --retrieval-repair 1 \
  --show-plan
```

## Privacy and security

- Default reports show only plan counters and source labels, not the raw plan.
- `--show-plan` is explicit and documented as potentially private.
- `--show-relevance` shows evidence ids and score categories, not raw snippets.
- Real evidence snippets remain hidden unless existing `--show-snippets` is
  also explicitly requested.
- Evidence text is treated as data, not instructions, in leader prompts.

## Performance and hardware

Planning adds one leader request per golden question when `--leader-plan` is
enabled. The default deterministic judge does not need GPU. Real leader
evidence judging is optional because it is slower and may expose private
evidence-derived content to the local model prompt.

## Rollback

Revert the files listed above. Existing Phase 8-J golden source and keyword
calibration should continue to work without planner options.

## Open questions

None blocking. Real leader-based evidence judging can be deepened in a later
phase after deterministic planner/reranker behavior is stable.
