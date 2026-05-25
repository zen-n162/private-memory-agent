# ExecPlan: Phase 9-A2 Answer Visibility UX

## Goal

Make the local `/ui` developer console feel like a useful chat interface by
showing generated answer text by default, while keeping raw evidence snippets
and raw model output hidden by default.

## Non-goals

- Do not make evidence snippets visible by default.
- Do not expose raw LINE text, note bodies, photo captions, OCR, filenames,
  paths, GPS, EXIF, or raw model output by default.
- Do not add authentication or public serving.
- Do not alter CLI E2E/golden defaults.

## Current state

- Phase 9-A added `/ui`, `POST /api/chat/query`, and
  `GET /api/system/status`.
- `show_answer` currently defaults to false in the UI and the chat query
  schema.
- The UI says only `Answer text hidden.` when the answer exists but is hidden,
  which is privacy-safe but confusing for a chat console.

## Proposed design

- Set `/ui` `show_answer` checkbox checked by default.
- Set the UI-facing `ChatQueryRequest.show_answer` and
  `ChatConsoleOptions.show_answer` defaults to true.
- Keep `show_snippets` false by default.
- Add `answer_hidden` and `answer_state` to the answer payload.
- Render answer states distinctly:
  - not generated / failed
  - generated but hidden
  - visible unknown / insufficient evidence
  - visible answer
- Update privacy copy to explain that answer text is local and visible by
  default, while raw evidence snippets remain opt-in private output.

## Data contracts

`POST /api/chat/query` answer payload adds:

- `answer_hidden: bool`
- `answer_state: "not_generated" | "hidden" | "unknown" | "visible"`

No raw evidence fields are added.

## Files to change

- `.agent/execplans/phase-9a2-answer-visibility-ux.md`
- `src/private_memory_agent/api/schemas.py`
- `src/private_memory_agent/api/console.py`
- `src/private_memory_agent/api/ui.py`
- `tests/test_api_console.py`
- `tests/test_api.py`
- `docs/API.md`
- `docs/ROADMAP.md`
- `docs/MODEL_RUNTIME.md`
- `docs/RETRIEVAL.md`
- `docs/overview_ja.html`

## Implementation steps

1. Change UI/API defaults for `show_answer` to true.
2. Add answer state metadata in the console response.
3. Update UI copy and answer rendering.
4. Update and add synthetic tests for default answer visibility and explicit
   hidden answer behavior.
5. Update docs and Japanese overview.
6. Run pytest and overview validation.

## Tests and verification

Run:

```bash
python -m pytest -q
python scripts/check_overview_html.py
pma api serve --help
```

Optionally run:

```bash
pma api serve --config configs/paths.local.yaml --host 127.0.0.1 --port 8787
```

Then open `http://127.0.0.1:8787/ui`.

## Privacy and security

Showing answer text by default is limited to the local developer console. The UI
copy warns that answer text may contain private evidence-derived information and
should not be pasted into public chats. Evidence snippets remain hidden unless
explicitly enabled, and raw model output remains unavailable in this UI path.

## Performance and hardware

No new GPU or model loading requirement. Defaults remain retrieval-only in the
UI, so answer display only affects fake/real model modes after the user selects
them.

## Rollback

Revert `show_answer` defaults to false and remove the answer-state UI copy.

## Open questions

None blocking.
