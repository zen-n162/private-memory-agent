# ExecPlan: Phase 4-B Leader Agent Minimal Query Flow

## Goal

Build a minimal local RAG query flow: retrieve evidence, pass the question and packed evidence to a leader agent, validate a structured answer, run an evidence critic, and print the final structured answer from `pma query`.

## Non-goals

- Do not require a real DeepSeek or other leader server in unit tests.
- Do not call external services.
- Do not implement autonomous planning, tool loops, multi-step reflection, memory writing, or follow-up actions.
- Do not let retrieved text act as instructions.
- Do not answer without local evidence.

## Current State

The repository already has:

- `RetrievalService` and `pma retrieve`.
- `ChatModelClient`, fake chat clients, and an OpenAI-compatible local HTTP client.
- Model registry endpoint metadata.
- Privacy-redacted CLI display behavior.

There is no leader agent, answer schema, grounding critic, or `pma query` command.

## Proposed Design

Add `private_memory_agent.agent.leader` with:

- `Answer` data model.
- `LeaderAgent` that accepts a `ChatModelClient`, question, and retrieved evidence.
- Strict JSON parsing and validation for model output.
- `EvidenceCritic` that validates grounding shape, including evidence references and source usage.
- `run_query_flow` helper that wires `RetrievalService` and `LeaderAgent`.

If retrieval returns no evidence, the flow returns a deterministic insufficient-evidence answer without calling the leader model.

Prompt design:

- System prompt says evidence is untrusted data, not instructions.
- User message includes question and packed evidence in delimiters.
- Model must return JSON only.

CLI:

- `pma query "question"`
- Default `--client openai-compatible --model-key leader`
- Unit/smoke path `--client fake`
- Options mirror retrieval basics: `--db`, `--limit`, `--source`, `--since`, `--until`, `--semantic-model`.

## Data Contracts

`Answer`:

- `conclusion: str`
- `evidence_references: tuple[str, ...]`
- `confidence: float`
- `unknowns: tuple[str, ...]`
- `used_sources: tuple[str, ...]`

Strict model JSON must have exactly:

```json
{
  "conclusion": "string",
  "evidence_references": ["line_messages:1"],
  "confidence": 0.0,
  "unknowns": ["string"],
  "used_sources": ["line"]
}
```

## Files to Change

- `.agent/execplans/phase-4b-leader-agent-minimal-query-flow.md`
- `docs/RETRIEVAL.md`
- `src/private_memory_agent/agent/__init__.py`
- `src/private_memory_agent/agent/leader.py`
- `src/private_memory_agent/cli.py`
- `tests/test_leader_agent.py`

## Implementation Steps

1. Add leader agent package and answer validation.
2. Add fake deterministic answer support through existing fake chat client.
3. Add prompt construction that isolates evidence from instructions.
4. Add evidence critic checks.
5. Add empty-evidence deterministic answer.
6. Add query flow helper over `RetrievalService`.
7. Add `pma query`.
8. Add tests with fake DB data and fake model output.
9. Run `pytest -q`.

## Tests and Verification

Run:

- `pytest -q`

Tests cover:

- Strict valid answer parsing.
- Rejection of malformed, extra-key, bad-confidence, or ungrounded references.
- Empty evidence returns insufficient evidence without model call.
- Prompt contains explicit evidence-as-data instruction.
- CLI `pma query` works with fake client and redacts display output by default.

## Privacy and Security

Retrieved evidence is treated as untrusted data in the prompt. CLI output is structured and can be redacted. The fake/default unit path uses no real private data. No external calls are made.

## Performance and Hardware

Default tests are CPU-only and model-free. Real leader use requires a user-started local OpenAI-compatible endpoint. No GPU assumptions are introduced by tests.

## Rollback

Remove the agent package, `pma query`, tests, and docs additions. Retrieval and previous annotations remain unchanged.

## Open Questions

None blocking.
