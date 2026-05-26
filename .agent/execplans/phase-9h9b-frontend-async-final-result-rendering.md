# Phase 9-H9b: Frontend Async Final Result Rendering

## Goal

Fix the local chat UI so async runs render the final `/result` payload after
the run completes. The UI must not render `/start`, `/status`, or
`ChatRunNotReady` payloads as final answer/candidate/evidence panels.

## Problem

Manual API checks show the backend async lifecycle is correct:

- `/api/chat/query/start` returns a run id and running status.
- `/api/chat/runs/{run_id}/status` eventually returns succeeded with a done
  completion summary.
- `/api/chat/runs/{run_id}/result` returns `ok=true`, `answer_succeeded=true`,
  candidate dates, evidence counts, and succeeded current status.

The UI can still show `ChatRunNotReady`, empty candidate dates/evidence, and a
failed status. That means the frontend is likely rendering an intermediate
payload as final output or fetching `/result` before it is ready.

## Plan

1. Audit `/ui` JavaScript run flow.
2. Add explicit async lifecycle helpers:
   - `/start` is status-only.
   - `/status` updates only the Current Status Bar.
   - `/result` is the only payload allowed to render Answer/Candidate/Evidence.
3. Treat `ChatRunNotReady` as pending while the run is queued/running.
4. Retry `/result` a few times after status succeeds to handle handoff lag.
5. Keep previous final panels visible/stale while a new run is running instead
   of replacing them with empty pending payloads.
6. Validate source selection before sending the request.
7. Add safe state-transition logging and a developer payload source marker.
8. Add regression tests using existing HTML/static UI test style.
9. Update docs and `docs/overview_ja.html`.
10. Run:
   - `python -m pytest -q`
   - `python scripts/check_overview_html.py`

## Privacy

Frontend debug logging must be metadata-only. It must not log raw evidence,
LINE text, note bodies, captions, filenames, GPS, EXIF, OCR, prompts, or raw
model output.
