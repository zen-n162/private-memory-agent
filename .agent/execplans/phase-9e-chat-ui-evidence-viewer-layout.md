# ExecPlan: Phase 9-E Chat UI Evidence Viewer And Candidate-Date Accordion Layout

## Goal

Make the localhost chat UI practical for inspecting temporal and multimodal
answers. Candidate dates should be readable accordion cards, supporting evidence
should be grouped by source and evidence role, photo thumbnails should be easy
to browse, snippets should be truncated and expandable, and machine reason codes
should have human-readable Japanese labels.

## Non-goals

- Do not add a frontend framework or external CDN.
- Do not expose the API beyond localhost defaults.
- Do not change retrieval ranking or model answer quality logic.
- Do not expose full photo paths, GPS, EXIF, raw OCR, raw LINE conversations,
  full note bodies, or raw model output by default.
- Do not serve arbitrary filesystem paths.

## Current state

- `/ui` is a self-contained FastAPI-served HTML page in
  `src/private_memory_agent/api/ui.py`.
- `POST /api/chat/query` returns `answer`, `evidence`, `trace`, `privacy`, and
  an additive `evidence_display` payload.
- `src/private_memory_agent/api/evidence_view.py` builds safe evidence display
  payloads and `GET /api/evidence/media/{media_item_id}/thumbnail` serves
  resized image thumbnails for indexed media IDs.
- Candidate dates are expandable cards, but source sections are still static,
  reason strings are machine-oriented, and thumbnail/snippet inspection needs
  clearer controls.

## Proposed design

Extend the API evidence display payload with:

- `reason_codes` and `reason_labels` for candidate dates and evidence items.
- role-separated lists: `used_evidence`, `candidate_evidence`,
  `rejected_evidence`.
- source-specific lists: `photos`, `line_snippets`, `note_snippets`.
- safe display metadata for snippets including `preview`, `full_preview`,
  `has_more`, and `snippet_chars`.

Update the UI:

- Candidate-date cards remain accordions.
- Each expanded date gets simple in-card source tabs: Photos, LINE, Notes,
  Rejected / Weak evidence.
- Photo grids show the first 6 thumbnails, with a `show more` button for
  additional thumbnails.
- Snippet cards show a short preview with `read more` / `collapse`.
- Evidence IDs and reason text use wrapping chips/blocks.
- Reason codes show Japanese labels while preserving machine codes.

## Data contracts

`evidence_display.candidate_dates[]` adds:

```json
{
  "reason_codes": ["..."],
  "reason_labels": ["..."],
  "used_evidence": [],
  "candidate_evidence": [],
  "rejected_evidence": [],
  "photos": [],
  "line_snippets": [],
  "note_snippets": []
}
```

Each evidence display item may add:

```json
{
  "reason_label": "...",
  "snippet_preview": "...",
  "snippet_full_preview": "...",
  "snippet_has_more": true,
  "snippet_chars": 160
}
```

No payload includes full paths, GPS, EXIF dumps, raw model output, or unrestricted
full private text.

## Files to change

- `src/private_memory_agent/api/evidence_view.py`
- `src/private_memory_agent/api/ui.py`
- `tests/test_api_evidence_view.py`
- `tests/test_api_console.py`
- `tests/test_temporal_events.py`
- `docs/API.md`
- `docs/ROADMAP.md`
- `docs/RETRIEVAL.md`
- `docs/MODEL_RUNTIME.md`
- `docs/overview_ja.html`

## Implementation steps

1. Add a human-readable reason mapping helper in the API display layer.
2. Add reason labels, role-separated evidence lists, source-specific lists, and
   snippet preview metadata to the `evidence_display` payload.
3. Update UI rendering with candidate-date accordions that contain source tabs.
4. Add thumbnail show-more behavior and snippet read-more/collapse behavior.
5. Ensure long IDs/reason strings wrap everywhere.
6. Add synthetic tests for reason mapping, snippets, role separation, and UI
   markup.
7. Update docs and Japanese overview.
8. Run full tests and overview validation.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

Tests must use synthetic data only and cover:

- candidate date accordion/source-tab markup
- photo thumbnail payload and validation
- LINE/note snippet truncation and read-more metadata
- human-readable reason labels
- `should_use=false` remains rejected/weak, not used
- privacy-safe default output and no full path leakage

## Privacy and security

- Thumbnail serving remains ID-based and path-free.
- The UI never requests arbitrary files.
- Snippets require explicit `show_snippets` and are truncated.
- Full text and raw model output remain off by default.
- Tests do not use real photos, LINE logs, notes, paths, GPS, EXIF, or OCR.

## Performance and hardware

No GPU or model server is required. Thumbnail rendering is bounded by the local
thumbnail max-side limit, and UI rendering initially shows only a small number
of thumbnails per candidate date.

## Rollback

Revert the payload additions, UI tab/show-more/read-more changes, tests, docs,
and this ExecPlan. The base chat query API remains backward-compatible because
new fields are additive.

## Open questions

None blocking.
