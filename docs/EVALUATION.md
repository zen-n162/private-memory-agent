# Evaluation

Phase 7-A adds a synthetic local evaluation harness for quality and safety
checks.

## Scope

The default evaluation uses only generated data:

- synthetic LINE messages
- synthetic notes
- synthetic photo metadata and annotations
- fake leader model client
- temporary or caller-provided SQLite database

It does not use real personal data, GPU, model files, model servers, Docker,
network access, or external APIs.

## Run

```bash
pma eval run
```

For a shorter output:

```bash
pma eval run --summary
```

For debugging with a chosen database path:

```bash
pma eval run --db /tmp/private-memory-agent-eval.sqlite3
```

The smoke script runs the same synthetic eval path:

```bash
python scripts/smoke_test.py
```

## Cases

The default suite covers:

- date questions
- person questions with uncertainty
- place questions
- insufficient evidence
- prompt injection inside notes
- LINE joke vs fact
- privacy redaction

Cases use synthetic marker terms so retrieval is deterministic with the current
FTS/LIKE text search and photo annotation matching.

## Metrics

- `evidence_recall_proxy`: expected synthetic evidence ids were retrieved and
  explicitly forbidden synthetic evidence ids were not retrieved.
- `groundedness_check`: answer references only retrieved evidence ids and known
  source kinds, keeps uncertainty for person-uncertainty cases, and does not
  surface injected instructions as the answer.
- `privacy_leak_check`: redacted display payloads do not contain known synthetic
  private markers.
- `insufficient_evidence_handling`: no-evidence cases return the deterministic
  insufficient-evidence answer with no evidence references.

These are deterministic proxy checks, not a full model-quality benchmark. Future
phases can add opt-in real-model evaluation once local model servers and private
review workflows are ready.

## Golden Questions

Phase 8-H adds an opt-in golden question workflow for existing local metadata.
Use the safe template:

```bash
configs/golden_questions.example.yaml
```

Put private local questions in:

```bash
configs/golden_questions.local.yaml
```

The local file is ignored by Git through `configs/*.local.yaml`. Keep ids
non-private if you intend to share reports.

Run the workflow in stages:

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only --query-limit 2 --json
pma eval golden --config configs/paths.local.yaml --fake-model --query-limit 2 --json
pma eval golden --config configs/paths.local.yaml --real-model --query-limit 1 --timeout-seconds 600 --max-tokens 512 --json
```

Reports include retrieval status, answer status, evidence ids, source counts,
used sources, confidence, unknown counts, JSON retry status, answer validation
errors, privacy-safe output status, and manual rating placeholders:

- answer_correctness
- evidence_relevance
- source_coverage
- uncertainty_handling
- privacy_safety
- source_policy_passed
- source_mismatch_notes
- irrelevant_evidence_notes
- notes

Golden questions can also declare source constraints:

```yaml
expected_sources: [line, notes]
required_sources: [notes]
preferred_sources: [line]
excluded_sources: [photos]
expected_keywords: [研究]
negative_keywords: []
evaluation_focus: [evidence_relevance, source_coverage]
```

The command line can apply the same constraints for a one-off check:

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only \
  --query-id qst_preparation \
  --require-source line \
  --require-source notes \
  --exclude-source photos \
  --json
```

`--source-policy strict` fails the evaluation when expected or required sources
are missing. The default `soft` policy records missing sources in diagnostics
without failing solely on source coverage. Excluded sources are filtered from
golden retrieval. Use `--show-snippets --snippet-chars N` only for local
relevance inspection.

Markdown and JSONL outputs can be written under ignored local paths:

```bash
pma eval golden --config configs/paths.local.yaml --retrieval-only --query-limit 2 \
  --output data/local/reports/golden_eval.md \
  --output-jsonl data/local/reports/golden_eval.jsonl
```

By default, golden reports do not print question text, answer text, snippets,
raw evidence, raw model output, filenames, full paths, GPS, EXIF, OCR, LINE
text, note bodies, or captions. Use `--show-answer` only for local review and
`--show-snippets` only for explicit snippet inspection.

## Privacy Defaults

Evaluation output does not include raw synthetic private note or LINE bodies, or
exact local SQLite paths. Real data roots and local config files are not read.
Prompt-injection and privacy checks use known synthetic strings only.
