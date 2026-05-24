#!/usr/bin/env python3

"""Local smoke test for private-memory-agent."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


def _add_src_to_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src = repo_root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def main() -> int:
    _add_src_to_path()

    from private_memory_agent.evaluation import run_synthetic_eval

    with tempfile.TemporaryDirectory(prefix="pma-smoke-") as temp_dir:
        db_path = Path(temp_dir) / "smoke.sqlite3"
        result = run_synthetic_eval(db_path=db_path, run_id="smoke")

    summary = result.summary_dict()
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if not result.passed:
        print("Smoke test failed: synthetic eval did not pass.", file=sys.stderr)
        return 1
    print("Smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
