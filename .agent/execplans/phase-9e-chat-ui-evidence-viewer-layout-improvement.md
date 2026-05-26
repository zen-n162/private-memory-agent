# ExecPlan: Phase 9-E Chat UI Evidence Viewer Layout Improvement

## Goal

Make the local `/ui` developer console usable as an evidence-first memory
inspection surface. The UI should show answers, candidate dates, evidence IDs,
safe photo thumbnails, and explicitly requested LINE/note snippets without
overflowing long fields or leaking private paths and raw metadata.

## Non-goals

- Do not build a large frontend framework.
- Do not expose the app beyond localhost.
- Do not serve arbitrary files from the filesystem.
- Do not show raw LINE text, note bodies, captions, filenames, GPS, EXIF, OCR,
  or raw model output by default.
- Do not change retrieval ranking or answer quality logic in this phase.

## Current state

- `src/private_memory_agent/api/ui.py` contains a self-contained HTML/CSS/JS
  console served at `/ui`.
- `src/private_memory_agent/api/app.py` exposes `POST /api/chat/query` and
  `GET /api/system/status`.
- `src/private_memory_agent/api/console.py` returns answer, trace, privacy, and
  basic evidence metadata.
- Temporal event queries already return `temporal_event.candidate_dates` and
  evidence roles, but the UI renders long evidence ID lists as single lines and
  cannot inspect evidence payloads visually.
- No thumbnail endpoint exists yet.

## Proposed design

Add a small API-layer evidence viewer helper that enriches only the evidence IDs
already selected by retrieval. It will query SQLite by ID, return safe display
payloads, and never expose raw source paths. The helper will also build
candidate-date evidence groups for temporal answers.

Add a safe thumbnail endpoint:

```text
GET /api/evidence/media/{media_item_id}/thumbnail
```

The endpoint validates the `media_item_id` against `media_items`, reads only the
indexed source path from SQLite, resizes the image in memory, strips metadata by
re-encoding JPEG, and returns image bytes. It never accepts a path parameter and
never includes private paths in error messages.

Update the UI to render:

- Answer summary with grouped evidence references.
- Candidate date cards using `<details>`.
- Supporting evidence grouped by photos, LINE, and notes.
- Thumbnail grid for photo evidence.
- Snippet cards only when `show_snippets` is enabled.
- Privacy controls for `show_photo_thumbnails`, `show_full_text`, and
  `show_raw_model_output`.

## Data contracts

`ChatQueryRequest` adds:

- `show_photo_thumbnails: bool = True`
- `show_full_text: bool = False`
- `show_raw_model_output: bool = False`

`POST /api/chat/query` may include:

```json
{
  "evidence_display": {
    "groups": {"photos": [], "line": [], "notes": [], "unknown": []},
    "evidence_reference_groups": {"photos": [], "line": [], "notes": []},
    "candidate_dates": [
      {
        "date": "YYYY-MM-DD",
        "confidence": 0.0,
        "reason": "...",
        "photo_count": 0,
        "annotated_photo_count": 0,
        "line_support_count": 0,
        "notes_support_count": 0,
        "used_evidence_count": 0,
        "supporting_photos": [],
        "supporting_line_snippets": [],
        "supporting_note_snippets": [],
        "candidate_evidence": [],
        "rejected_evidence": []
      }
    ],
    "privacy": {
      "snippets_hidden": true,
      "full_text_hidden": true,
      "paths_hidden": true,
      "gps_hidden": true
    }
  }
}
```

Safe photo payloads include `evidence_id`, `media_item_id`, `thumbnail_url`,
`taken_at`, `media_type`, and safe dimensions. Annotation summaries are included
only when snippets are explicitly enabled.

Safe LINE/note payloads include IDs, timestamps, and truncated snippets only
when snippets are explicitly enabled. Note titles are treated as private display
content and hidden unless snippets are enabled.

## Files to change

- `src/private_memory_agent/api/evidence_view.py` (new)
- `src/private_memory_agent/api/console.py`
- `src/private_memory_agent/api/app.py`
- `src/private_memory_agent/api/schemas.py`
- `src/private_memory_agent/api/ui.py`
- `tests/test_api_console.py`
- `tests/test_api_evidence_view.py` (new)
- `docs/ROADMAP.md`
- `docs/RETRIEVAL.md`
- `docs/MODEL_RUNTIME.md` if needed
- `docs/overview_ja.html`

## Implementation steps

1. Add `evidence_view.py` with privacy-safe evidence payload builders,
   truncation, grouping, and thumbnail generation helpers.
2. Add new chat request/options fields and pass them from FastAPI to the console
   service.
3. Attach `evidence_display` to chat query payloads for temporal and normal
   query flows.
4. Add the validated thumbnail route in the FastAPI app.
5. Update the HTML/CSS/JS layout to wrap long fields, render candidate date
   cards, render grouped evidence, and show thumbnails/snippets according to
   controls.
6. Add synthetic tests for helper payloads, thumbnail validation, UI defaults,
   and privacy-safe output.
7. Update docs and the Japanese overview.
8. Run verification and commit/push only intended files.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

Add tests covering:

- Long evidence IDs are rendered through wrap-friendly chip/list elements.
- UI defaults: `show_photo_thumbnails` on, `show_snippets` off.
- Candidate date display payload groups evidence by date/source.
- Used, candidate, and rejected evidence remain distinct.
- Photo thumbnail payloads and endpoint helpers do not expose paths.
- Snippets are hidden by default and truncated when explicitly enabled.

## Privacy and security

- The thumbnail route accepts only `media_item_id`, never file paths.
- Thumbnail errors use safe messages only.
- SQLite display payloads redact paths, GPS, EXIF, and full metadata.
- Snippets and note titles are hidden unless explicitly requested.
- Full text and raw model output remain off by default.
- Tests use synthetic data and synthetic images only.

## Performance and hardware

No GPU is required. Thumbnail generation uses Pillow if available in the
existing environment and only for selected evidence IDs. Images are resized in
memory to small JPEG thumbnails.

## Rollback

Revert the new evidence-view helper, route, request fields, UI rendering
changes, tests, and docs. Existing `/api/chat/query` basic payloads remain
backward-compatible because `evidence_display` is additive.

## Open questions

None blocking. If Pillow is unavailable in a runtime environment, the thumbnail
endpoint should return a safe unsupported response while the rest of the UI
continues to work.
