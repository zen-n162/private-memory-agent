# Phase 9-J: Leader-Guided Visual Evidence Search and Photo Gallery Answers

## Goal

Support visual/photo-oriented questions such as
`ラーメンが写っている写真はどれ？` with a photo-gallery answer path instead of
forcing them through temporal candidate-date logic.

## Plan

1. Add a `visual_evidence_search` query path with a structured
   `VisualEvidencePlan`.
2. Support DeepSeek Leader visual planning with strict JSON and a deterministic
   fallback that exposes `fallback_used`.
3. Search cached photo annotations with event-specific visual/textual signals,
   optionally accepting semantic controls without requiring real models in
   tests.
4. Judge photo candidates into used, candidate, and rejected groups without
   requiring candidate dates.
5. Return a visual answer with `matching_photo_count`, thumbnail payloads,
   safe diagnostics, and runtime trace events.
6. Render a Matching Photos panel in `/ui` when
   `query_type=visual_evidence_search`.
7. Keep raw private evidence, paths, GPS, EXIF, OCR, raw prompts, and raw model
   output hidden by default.
8. Add synthetic tests for classification, planning, annotation search,
   structured unknown, thumbnail payloads, trace, and UI routing.
9. Update docs and `docs/overview_ja.html`.

## Privacy

Default output may include evidence IDs, source labels, counts, thumbnail URLs
for indexed media IDs, timestamps, and short safe annotation summaries only when
existing UI controls allow it. It must not include full filesystem paths, GPS,
EXIF dumps, raw OCR text, LINE bodies, note bodies, raw prompts, or raw model
outputs.

## Verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

Optional local checks:

```bash
pma api serve --config configs/paths.local.yaml --host 127.0.0.1 --port 8787
```

Then query `/api/chat/query` or `/ui` with:

```text
ラーメンが写っている写真はどれ？
```
