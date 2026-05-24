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

## Privacy Defaults

Evaluation output does not include raw synthetic private note or LINE bodies, or
exact local SQLite paths. Real data roots and local config files are not read.
Prompt-injection and privacy checks use known synthetic strings only.
