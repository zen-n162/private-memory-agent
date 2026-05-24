# ExecPlan: Phase 8-J Golden Keyword Calibration And Evidence Relevance

## Goal

Make golden question evaluation measure evidence relevance, not only source
coverage. Golden questions can declare expected, optional, and negative
keywords. Retrieval for golden evaluation can use those keywords to expand the
retrieval query, boost matching evidence, penalize negative matches, and report
privacy-safe relevance diagnostics.

## Non-goals

- Do not add new ingestion or indexing formats.
- Do not change default E2E smoke behavior for non-golden commands.
- Do not print raw LINE text, note bodies, captions, filenames, paths, GPS,
  EXIF, OCR, or raw model output by default.
- Do not require real local models, GPU, network, or private data in tests.
- Do not put private names or facts into public example configs.

## Current state

- `pma eval golden` supports retrieval-only, fake-model, and real-model modes.
- Phase 8-I added expected/required/preferred/excluded source constraints,
  strict/soft source policy, snippet length control, and source diagnostics.
- `RetrievalService` ranks FTS, LIKE, semantic, and media annotation evidence,
  with source balancing across requested sources.
- `configs/golden_questions.example.yaml` has safe example questions with
  `expected_keywords` and `negative_keywords`, but these fields are currently
  only counted in diagnostics.
- E2E and golden default output are privacy-safe.

## Proposed design

- Extend `GoldenQuestion` with `optional_keywords`.
- Add CLI repeatable flags:
  - `--expected-keyword`
  - `--negative-keyword`
  - `--keyword-policy soft|strict`
- For golden evaluation only, build an expanded retrieval string from the
  original question plus expected/optional keyword hints. The leader prompt
  still receives the original question.
- Extend `E2ESmokeQuery` with:
  - `retrieval_text`
  - `boost_terms`
  - `negative_terms`
- Extend `RetrievalFilters` with boost/negative terms. `RetrievalService`
  adjusts candidate scores before source-balanced ranking.
- Compute keyword diagnostics inside E2E from raw in-memory evidence, then
  return only counts and evidence ids:
  - expected keyword counts and missing expected keywords
  - evidence keyword hit counts by evidence id
  - negative keyword hit count
- Golden evaluation converts those diagnostics into a deterministic
  `relevance_score`.

## Data contracts

`GoldenQuestion` adds:

- `optional_keywords: tuple[str, ...]`

`GoldenEvalOptions` adds:

- `expected_keywords: tuple[str, ...]`
- `negative_keywords: tuple[str, ...]`
- `keyword_policy: str`

`GoldenQuestionResult` adds:

- `optional_keywords_count`
- `expected_keywords_hit_count`
- `expected_keyword_hit_evidence_count`
- `missing_expected_keywords`
- `negative_keyword_hit_count`
- `evidence_keyword_hit_counts`
- `relevance_score`
- `keyword_policy`
- `retrieval_passed_keyword_policy`

`E2ESmokeQuery` adds:

- `retrieval_text: str | None`
- `boost_terms: tuple[str, ...]`
- `negative_terms: tuple[str, ...]`

## Files to change

- `src/private_memory_agent/evaluation/golden.py`
- `src/private_memory_agent/e2e.py`
- `src/private_memory_agent/retrieval/evidence.py`
- `src/private_memory_agent/cli.py`
- `configs/golden_questions.example.yaml`
- `tests/test_golden_evaluation.py`
- `docs/RETRIEVAL.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add keyword fields to golden question loading, options, and CLI.
2. Pass expanded retrieval text and keyword terms into E2E queries from golden
   evaluation only.
3. Add retrieval score adjustment using boost and negative terms.
4. Add privacy-safe keyword diagnostics to E2E query results.
5. Add golden relevance scoring and Markdown/JSON report fields.
6. Add synthetic tests for parsing, CLI flags, ranking, missing/negative
   keyword diagnostics, relevance scoring, and privacy.
7. Update public example config and docs.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

If the local DB exists, also run:

```bash
pma eval golden \
  --config configs/paths.local.yaml \
  --retrieval-only \
  --query-id qst_preparation \
  --require-source line \
  --require-source notes \
  --exclude-source photos \
  --expected-keyword QST \
  --expected-keyword 面接 \
  --expected-keyword 内定 \
  --json
```

If the local leader server is running, run one real-model check:

```bash
pma eval golden \
  --config configs/paths.local.yaml \
  --real-model \
  --query-id qst_preparation \
  --require-source line \
  --require-source notes \
  --exclude-source photos \
  --expected-keyword QST \
  --expected-keyword 面接 \
  --expected-keyword 内定 \
  --timeout-seconds 600 \
  --max-tokens 512 \
  --show-answer
```

## Privacy and security

- Keyword diagnostics output keywords from user-provided config or CLI, but never
  evidence text.
- Raw snippets remain hidden unless `--show-snippets` is explicitly used.
- Snippets are truncated and path/GPS-like content is redacted by existing E2E
  display safeguards.
- Public example keywords remain generic and contain no private names or facts.

## Performance and hardware

No GPU or model-server requirement is introduced. Keyword scoring is
deterministic string matching over already retrieved candidates. It is intended
for smoke/evaluation scale, not heavy corpus-wide analysis.

## Rollback

Revert changes in the files listed above. Existing golden source constraints
and E2E smoke behavior should return to Phase 8-I behavior.

## Open questions

None blocking. Relevance scoring is intentionally simple and may be calibrated
further after inspecting local-only snippet output.
