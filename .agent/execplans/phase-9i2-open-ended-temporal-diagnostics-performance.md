# Phase 9-I2: Open-ended Temporal Diagnostics, Subtype Planning, and Performance

## Goal

Make open-ended temporal event queries such as
`ラーメンを食べに行っているのはいつ？` diagnosable, faster, and clearer in the
UI/API contract.

## Plan

1. Expose temporal diagnostics at the top level in addition to
   `temporal_event.diagnostics` and `trace.temporal_diagnostics`.
2. Carry `event_subtype`, event intent signal counts, safe signal previews, and
   `generated_by` through temporal payloads and UI diagnostics.
3. Add a staged open-ended search strategy:
   - infer full local coverage bounds;
   - scan a recent bounded window first;
   - stop if enough candidate dates are found;
   - otherwise expand to all available memory with caps.
4. Add performance diagnostics:
   - timing by stage;
   - chunks scanned;
   - candidate counts before/after pruning;
   - evidence counts by source;
   - dated/undated evidence conversion rate.
5. Improve answer wording for unspecified date scopes.
6. Add Japanese UI reason labels for event-specific evidence reason codes.
7. Add synthetic tests for subtype exposure, diagnostics, caps, structured
   unknowns, and bounded query compatibility.
8. Update docs and `docs/overview_ja.html`.

## Privacy

Diagnostics must remain metadata-only. Do not print raw LINE text, note bodies,
photo captions, filenames, paths, GPS, EXIF, OCR, raw prompts, or raw model
outputs.

## Verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
```

Optional local checks:

```bash
pma query "ラーメンを食べに行っているのはいつ？" --config configs/paths.local.yaml --temporal-diagnostics
pma query "2025年12月で、ご飯を食べに行っているのはいつ？" --config configs/paths.local.yaml --temporal-diagnostics
```
