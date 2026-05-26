# ExecPlan: Phase 9-G Leader-Guided Event Intent Planning

## Goal

Make temporal event queries understand event-specific intent, not only generic
outing intent. A query like `2025年12月でご飯を食べに行っているのはいつ？` should be
recognized as a December 2025 dining-out event search, use dining-specific
visual/textual signals, cluster candidate dates, and return privacy-safe
candidate evidence.

## Non-goals

- Do not hard-code one private query or one user's private facts.
- Do not require DeepSeek, GPU, or model servers in unit tests.
- Do not expose raw LINE text, note bodies, captions, filenames, full paths,
  GPS, EXIF, OCR, or raw model output by default.
- Do not replace existing source constraints, chunking, pruning, or temporal
  coverage diagnostics.

## Current state

`src/private_memory_agent/temporal.py` parses temporal outing questions, searches
photos by `media_items.taken_at`, scores generic outing likelihood, clusters by
day, adds LINE/notes support, and emits count-only diagnostics. It now supports
season/year/month ranges and Japanese month ranges. The temporal path still
uses mostly generic outing terms, so a dining-style query can fail to enter or
score the temporal event path.

The UI already renders candidate dates, evidence display payloads, and temporal
diagnostics through `src/private_memory_agent/api/console.py` and
`src/private_memory_agent/api/ui.py`.

## Proposed design

Add an `EventIntentPlan` schema and planner abstraction. The deterministic
fallback planner will infer open-vocabulary event types such as `outing`,
`dining_out`, `travel`, `shopping`, `meeting`, `work`, `research`, or
`unknown_event`. It will provide:

- visual signals
- textual signals
- source priorities
- positive/weak/negative criteria
- candidate date policy
- repair queries

The first implementation layers this plan into the existing temporal workflow:

1. Parse the date range structurally.
2. Infer an event intent plan.
3. Search photos by date range.
4. Score photo annotations against generic outing terms plus plan-specific
   visual signals.
5. Search LINE/notes using plan textual signals.
6. Cluster by day and add plan-aware matched signal diagnostics.
7. Separate used/candidate/rejected evidence as before.

Real Leader planning can be wired through the same schema later without changing
the temporal result contract. Unit tests will use deterministic/fake planners.

## Data contracts

`EventIntentPlan` fields:

- `query_type`
- `date_range`
- `event_type`
- `event_description`
- `visual_signals`
- `textual_signals`
- `source_priorities`
- `source_constraints`
- `positive_evidence_criteria`
- `weak_evidence_criteria`
- `negative_evidence_criteria`
- `candidate_date_policy`
- `repair_queries`
- `fallback_used`

Temporal diagnostics add:

- `event_type`
- `event_description`
- `visual_signal_count`
- `textual_signal_count`
- `source_priorities`
- `candidate_date_count`
- `event_intent_plan_created`
- `event_intent_fallback_used`
- `event_score_by_date`
- `matched_visual_signals_by_date`
- `matched_textual_signals_by_date`
- `repair_attempted`
- `repair_reason`

## Files to change

- `src/private_memory_agent/temporal.py`
- `src/private_memory_agent/api/ui.py`
- `tests/test_temporal_events.py`
- `docs/RETRIEVAL.md`
- `docs/ROADMAP.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add `EventIntentPlan` and deterministic planner in `temporal.py`.
2. Broaden temporal query detection so dining/travel/shopping/meeting style
   event questions enter the temporal workflow.
3. Apply plan visual/textual signals to photo scoring and LINE/notes fallback.
4. Add event score and matched signal metadata to daily clusters.
5. Emit UI-safe event intent diagnostics.
6. Add tests with synthetic dining photos and LINE messages.
7. Update docs and overview.
8. Run verification.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

If the local UI runs, ask:

```text
2025年12月でご飯を食べに行っているのはいつ？
```

Verify event intent diagnostics are visible and no raw private content is shown.

## Privacy and security

All default outputs remain metadata/count/ID focused. Planner diagnostics show
signal counts and short configured signal labels, not raw evidence text. Tests
use synthetic data only.

## Performance and hardware

No GPU is required. The deterministic planner is cheap. Event-specific term
matching reuses existing SQLite and text normalization paths.

## Rollback

Revert the EventIntentPlan additions, restore generic outing scoring only, and
remove the UI diagnostics/tests/docs added in this phase.

## Open questions

None blocking.
