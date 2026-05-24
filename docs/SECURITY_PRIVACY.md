# Security and Privacy

This document outlines security and privacy considerations for private-memory-agent.

## Topics

- data protection
- access controls
- encryption
- privacy-first design

## Query Guardrails

`pma query` uses deterministic guardrails before printing a final answer:

- Evidence is treated as untrusted data, not instructions.
- Answers must reference retrieved evidence ids only.
- Weak evidence requires uncertainty and low confidence.
- Prompt-injection phrases inside notes or LINE exports must not appear as
  obeyed instructions in the final answer.
- Display output is redacted by default.

`PrivacyGuard` can redact likely third-party names, reduce GPS precision in
metadata, and mark evidence as sensitive. Log helpers fail closed when a message
would include known raw private fragments.

## Human-Confirmed Identity Policy

Private Memory Agent separates identity candidates from confirmed identities.

- Extracted person names are evidence mentions, not proof of identity.
- Unconfirmed person mentions become `person_unknown_*` candidates.
- The app does not assert face-to-name identity automatically.
- People are not merged from weak signals or similar names.
- A user-confirmed alias is required before an unknown person candidate can be
  merged into a named entity.
- Entity CLI output redacts names and aliases by default.

`pma entities alias add` is the Phase 5-B confirmation path. It records the alias
as user-confirmed and can merge matching same-type candidates into the selected
entity. This is still local-only and does not expose private names in command
output.

## Local API And UI

Phase 6-A adds a FastAPI API for localhost use only. Phase 6-B adds a minimal
HTML UI served by the same local app.

- Default bind is `127.0.0.1`.
- `pma api serve` rejects non-loopback hosts.
- There is no authentication yet.
- API responses are redacted by default.
- Ingest endpoints return count-only summaries.
- The UI only renders API responses and defaults to redacted snippets.
- The UI private display toggle is only a request; backend config must also
  allow private display.

Do not expose the API through a public proxy, tunnel, or shared host until a
future phase adds authentication and explicit access controls.
