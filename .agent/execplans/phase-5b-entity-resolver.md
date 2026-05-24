# ExecPlan: Phase 5-B Entity Resolver

## Goal

Add a deterministic entity resolver that tracks people, places, organizations,
topics, aliases, and evidence links without unsafe identity assumptions. Add CLI
commands to list entities and add user-confirmed aliases.

## Non-goals

- Do not assert face identity or face-to-name matches.
- Do not merge people automatically from weak model output.
- Do not call LLMs, vision models, embedding models, network services, or APIs.
- Do not ingest real data or add private fixtures.
- Do not print private names, aliases, LINE text, note text, filenames, or GPS in
  normal command output.

## Current state

The SQLite schema already includes:

- `entities`: `entity_type`, `canonical_name`, `display_name`, `metadata_json`,
  privacy flags, and timestamps.
- `evidence_links`: generic links between targets and evidence rows.
- `text_annotations`: validated extracted entities and topics for LINE messages
  and notes.

There is no entity resolver, alias API, user-confirmation policy, or CLI for
entities.

## Proposed design

Add `private_memory_agent.entities.resolver` with:

- `EntityResolver`: resolves text-annotation mentions into local entity rows and
  links source evidence.
- `EntityMention`: normalized input mention.
- `EntityResolveResult`: count-only result for resolver runs.
- `AliasAddResult`: count-only result for user alias additions.
- `list_entities`: privacy-safe entity listing.

Resolution policy:

- Entity types normalize to `person`, `place`, `organization`, or `topic`.
- Places, organizations, and topics may reuse existing rows by normalized alias.
- People from unconfirmed extracted text become `person_unknown_*` candidates
  scoped to the evidence mention.
- A person mention may reuse a confirmed person only when a user-confirmed alias
  already matches.
- `pma entities alias add` is a user-confirmed action. It adds an alias to a
  target entity, marks the target confirmed, and can merge same-type duplicate
  candidates that match the alias.

No schema migration is required. Alias state and confirmation flags live in
`entities.metadata_json`.

## Data contracts

`entities.metadata_json` for Phase 5-B:

- `phase`: `5-B`
- `aliases`: list of aliases
- `alias_norms`: normalized aliases
- `user_confirmed`: bool
- `identity_status`: `candidate` or `confirmed`
- `candidate_kind`: optional, such as `person_unknown`
- `source`: resolver/manual
- `merged_into_entity_id`: set only on soft-excluded merged rows

`evidence_links`:

- `target_table = "entities"`
- `target_id = entities.id`
- `evidence_table`: source table such as `line_messages` or `notes`
- `evidence_id`: source row id
- `relation_type = "mentions"`

CLI:

- `pma entities resolve`
- `pma entities list`
- `pma entities alias add <entity-id> <alias>`

## Files to change

- `.agent/execplans/phase-5b-entity-resolver.md`
- `src/private_memory_agent/entities/__init__.py`
- `src/private_memory_agent/entities/resolver.py`
- `src/private_memory_agent/storage/repositories.py`
- `src/private_memory_agent/storage/__init__.py`
- `src/private_memory_agent/cli.py`
- `tests/test_entity_resolver.py`
- `docs/DATA_MODEL.md`
- `docs/ARCHITECTURE.md`
- `docs/SECURITY_PRIVACY.md`

## Implementation steps

1. Add repository helpers for inserting/updating entities.
2. Implement resolver dataclasses and normalization rules.
3. Implement text-annotation resolution and evidence linking.
4. Implement alias addition and user-confirmed same-type merging.
5. Implement privacy-safe entity listing.
6. Add CLI entity resolve, list, and alias-add commands.
7. Add tests for alias merging, non-merging of unconfirmed people, evidence
   links, and privacy-safe CLI output.
8. Update docs and run verification.

## Tests and verification

- `pytest -q`
- `pma entities resolve --help`
- `pma entities list --help`
- `pma entities alias add --help`

Tests use temporary SQLite databases and synthetic names only. No GPU, model
files, Docker, network, model server, or private data is required.

## Privacy and security

Names and aliases may be private. CLI list output redacts them by default and
only shows private values if `--show-private` is used with config
`log_private_data = true`. Unconfirmed people remain `person_unknown_*`
candidates and are not merged. User-confirmed alias addition is the safe path
for merging candidate identities.

## Performance and hardware

The resolver scans local SQLite rows and small JSON metadata. No GPU/VRAM is
used. The default NVIDIA RTX 4500 Ada 24GB target is irrelevant for this phase.

## Rollback

Remove the entities package, CLI entity commands, tests, and doc updates. The
existing `entities` and `evidence_links` tables can remain because they are part
of the earlier schema. Locally generated entity rows can be soft-excluded.

## Open questions

None blocking. Future phases can add a human review UI for confirming or
splitting candidates.
