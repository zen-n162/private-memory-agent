# ExecPlan: Phase 4-C Evidence Critic And Privacy Guard

## Goal

Prevent overconfident, ungrounded, or privacy-leaking answers in the minimal
local RAG query flow. `pma query` should retrieve evidence, build an answer,
run deterministic evidence and privacy guardrails, and print a structured
redacted result by default.

## Non-goals

- Do not add an LLM-based critic.
- Do not add autonomous planning, tool loops, or memory writing.
- Do not send data to external services.
- Do not change ingestion behavior or read real private source roots in tests.
- Do not require GPU, model files, Docker, network, or real local model servers.

## Current state

Phase 4-B added `private_memory_agent.agent.leader` with `Answer`,
`LeaderAgent`, a fake leader client, a basic `EvidenceCritic`, and
`run_query_flow`. The current critic validates that answer references point to
retrieved evidence ids and source kinds. CLI output is redacted by default, but
privacy logic is spread across `Answer.to_dict`, `Evidence.to_dict`, and
`QueryFlowResult.to_dict`.

`RetrievalService` returns `Evidence` objects with snippets, source ids,
confidence, score, and metadata. Packed evidence is sent to the local leader
client as untrusted data.

## Proposed design

Add `private_memory_agent.agent.guardrails` containing two deterministic
services:

- `PrivacyGuard`: marks sensitive evidence, redacts display payloads, optionally
  redacts likely third-party names, optionally reduces GPS precision, and offers
  a fail-closed helper for log messages that might include raw private text.
- `EvidenceCritic`: validates answer grounding beyond schema shape. It checks
  known evidence ids, source consistency, evidence-backed claims, uncertainty
  for weak evidence, and obvious source-injected instructions copied into the
  answer.

`LeaderAgent` will accept an optional critic and use it after strict JSON
parsing. `run_query_flow` will accept an optional privacy guard and apply it to
the final result. `pma query` will construct these services from CLI/config and
continue to redact by default.

The previous public imports from `private_memory_agent.agent` remain available.

## Data contracts

New internal dataclasses:

- `PrivacyGuardPolicy`
  - `redact_names: bool`
  - `redact_gps_precision: bool`
  - `gps_decimal_places: int`
  - `block_private_logs: bool`
- `PrivacyGuard`
  - `mark_sensitive_evidence(evidence) -> tuple[Evidence, ...]`
  - `redact_answer(answer) -> Answer`
  - `redact_question(question) -> str`
  - `safe_log_message(message, private_fragments=()) -> str`
- `CriticPolicy`
  - `weak_confidence_threshold: float`
  - `max_confidence_for_weak_evidence: float`
  - `require_uncertainty_for_weak_evidence: bool`
- `CriticIssue`
  - `code: str`
  - `message: str`
  - `severity: str`

Existing contracts remain:

- `Answer`
  - `conclusion`
  - `evidence_references`
  - `confidence`
  - `unknowns`
  - `used_sources`
- `QueryFlowResult.to_dict()`
  - returns structured JSON-safe output, redacted by default.

## Files to change

- `.agent/execplans/phase-4c-evidence-critic-privacy-guard.md`
- `src/private_memory_agent/agent/guardrails.py`
- `src/private_memory_agent/agent/leader.py`
- `src/private_memory_agent/agent/__init__.py`
- `src/private_memory_agent/cli.py`
- `src/private_memory_agent/retrieval/evidence.py`
- `src/private_memory_agent/retrieval/__init__.py`
- `tests/test_guardrails.py`
- `tests/test_leader_agent.py`
- `docs/RETRIEVAL.md`
- `docs/SECURITY_PRIVACY.md`

## Implementation steps

1. Add guardrail dataclasses and deterministic redaction/injection helpers.
2. Move/replace the basic critic with the richer `EvidenceCritic` service while
   keeping the same import name for existing tests.
3. Add privacy guard integration to `QueryFlowResult.to_dict`.
4. Wire `pma query` to construct `PrivacyGuard` and the enhanced critic.
5. Add tests for injected note bodies, LINE injection text, weak evidence, and
   third-party name redaction.
6. Update retrieval/privacy docs.
7. Run `pytest -q` and CLI smoke checks.

## Tests and verification

- `pytest -q`
- `pma query --help`
- A fake-client query smoke using artificial fixture data only.

Unit tests must use temporary SQLite databases or synthetic `Evidence` objects.
They must not require GPU, model files, private data, Docker, or network access.

## Privacy and security

Evidence text is treated as untrusted data. Prompt-injection strings inside
notes or LINE messages are allowed to be retrieved but must not be obeyed or
copied as instructions. CLI output stays redacted by default. Name redaction and
GPS precision reduction are deterministic and opt-in at the guard policy level.
The log helper replaces any message containing known private fragments instead
of trying to sanitize it partially.

## Performance and hardware

The guardrails use simple string and regex rules over retrieved evidence and
answer text. No GPU, VRAM, model loading, or network is required. The default
target hardware is unaffected.

## Rollback

Remove `agent/guardrails.py`, revert the leader/CLI/doc/test changes, and restore
the Phase 4-B `EvidenceCritic` implementation in `leader.py`. Existing storage,
retrieval, ingestion, and model runtime code can remain unchanged.

## Open questions

None blocking. Future phases can add model-assisted claim decomposition and
critic scoring, but deterministic rules are sufficient for Phase 4-C.
