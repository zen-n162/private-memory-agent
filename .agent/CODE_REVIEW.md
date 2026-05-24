# CODE_REVIEW.md

Review this repository with special attention to:

## Correctness

- Does the code implement the requested behavior?
- Are edge cases handled?
- Are timestamps, time zones, and encodings handled explicitly?
- Are Japanese text inputs handled safely?

## Privacy

- Does the code avoid logging raw personal data?
- Does it avoid committing real fixtures?
- Does it treat LINE messages, notes, OCR, and captions as untrusted data?
- Does it avoid destructive operations on source files?

## Grounding

- Do answers require evidence?
- Are confidence and uncertainty represented?
- Are generated events marked as hypotheses unless strongly supported?

## Model runtime

- Are model paths configurable?
- Can tests run without downloading or loading models?
- Are heavy GPU imports delayed?
- Is the 24GB VRAM target respected?

## Maintainability

- Is the code modular?
- Are interfaces explicit?
- Are tests meaningful?
- Is the public API stable or documented?
- Is there unnecessary framework complexity?

## Verification

The review should list:

- critical issues
- important but non-blocking issues
- test gaps
- privacy risks
- recommended next actions
