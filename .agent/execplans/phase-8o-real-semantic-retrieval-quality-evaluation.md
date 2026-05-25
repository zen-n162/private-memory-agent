# ExecPlan: Phase 8-O Real Semantic Retrieval Quality Evaluation

## Goal

Add a privacy-safe semantic retrieval comparison workflow that compares text,
hash semantic, real local embedding, reranker, and leader-planned retrieval
configurations by usable evidence quality rather than candidate count alone.

## Non-goals

- Do not load real embedding or reranker models in unit tests.
- Do not run real embedding indexing by default.
- Do not change ingestion, annotation, or source data.
- Do not print raw LINE text, note bodies, captions, filenames, paths, GPS,
  OCR, EXIF, raw model output, or raw retrieval plan text by default.

## Current state

Phase 8-N added real local embedding aliases, source-filtered/resume-safe
embedding indexing, optional reranker controls, and semantic retrieval metrics.
Golden evaluation can run one configuration at a time, but there is no command
that compares retrieval configurations and makes it clear when quality judging
did not run.

## Proposed design

Add `pma eval semantic-compare` backed by a new evaluation module that runs
`run_golden_eval` repeatedly with a fixed set of retrieval configurations:

- `text_only`
- `hash_semantic`
- `ruri_v3_310m`
- `ruri_v3_310m_plus_reranker`
- `leader_plan_ruri`
- `leader_plan_ruri_plus_reranker`

The comparison report will carry privacy-safe per-configuration metrics and
mark `quality_judged=false` whenever leader-plan/relevance judging did not run.
Recommendation selection will prefer strict pass, usable evidence count, final
relevance score, source coverage, and privacy-safe defaults.

## Data contracts

New dataclasses:

- `SemanticCompareOptions`
- `SemanticCompareConfigResult`
- `SemanticCompareQueryResult`
- `SemanticCompareReport`
- `EmbeddingDeviceStatus`

JSON output contains only IDs, counts, source labels, scores, booleans, warnings,
and recommendations.

## Files to change

- `src/private_memory_agent/evaluation/semantic_compare.py`
- `src/private_memory_agent/evaluation/__init__.py`
- `src/private_memory_agent/cli.py`
- `src/private_memory_agent/retrieval/embeddings.py`
- tests for semantic comparison and CLI behavior
- `docs/RETRIEVAL.md`
- `docs/MODEL_RUNTIME.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add semantic comparison dataclasses and runner that wraps golden evaluation.
2. Add CLI command `pma eval semantic-compare`.
3. Add embedding device option plumbing for real embedding model construction.
4. Add CPU/CUDA-safe device diagnostics based on explicit option and optional
   torch availability.
5. Add tests with synthetic data only, using fake/hash paths.
6. Update docs and overview.
7. Run verification.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

Manual local checks when data/models are available:

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only \
  --query-id qst_preparation \
  --leader-plan \
  --leader-rerank \
  --retrieval-repair 1 \
  --semantic \
  --semantic-model ruri-v3-310m \
  --minimum-relevance-score 0.6 \
  --require-usable-evidence \
  --relevance-policy strict \
  --json

pma eval semantic-compare \
  --config configs/paths.local.yaml \
  --query-id qst_preparation \
  --json
```

## Privacy and security

The comparison command reuses golden evaluation's redaction and no-fallback
behavior. It does not show question text, snippets, plans, answers, or raw model
outputs by default. `--show-relevance` remains explicit.

## Performance and hardware

Real embedding and reranker configurations may be CPU/GPU backed depending on
the local PyTorch/SentenceTransformers installation. `--embedding-device cpu`
lets the user avoid CUDA driver warnings. Unit tests use fake/hash only.

## Rollback

Revert the new semantic comparison module, CLI command, docs updates, and tests.
Existing single-configuration golden evaluation and semantic retrieval remain
available.

## Open questions

None blocking.
