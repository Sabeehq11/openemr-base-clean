#!/usr/bin/env python3
"""Fail when mypy reports an error that is not already recorded.

mypy 1.15 has no baseline flag. scripts/w2/mypy-baseline.txt is the current error list, one
`file:line: error:` line per finding. A new line fails the gate. Removing a line is allowed, so
fixing an old error does not require editing the baseline first.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = Path(__file__).resolve().parent / "mypy-baseline.txt"


def errors(text: str) -> set[str]:
    return {line for line in text.splitlines() if ": error:" in line}


def main() -> int:
    run = subprocess.run(
        [sys.executable, "-m", "mypy", "--ignore-missing-imports", "--follow-imports=silent",
         "--no-error-summary", "agent/app", "agent/contracts"],
        cwd=ROOT, capture_output=True, text=True)
    if run.returncode not in (0, 1):
        sys.stderr.write(run.stderr)
        print(f"typecheck: mypy exited {run.returncode}")
        return run.returncode or 1
    current = errors(run.stdout)
    recorded = errors(BASELINE.read_text())
    new = sorted(current - recorded)
    if new:
        print(f"typecheck: {len(new)} new error(s)")
        print("\n".join(new))
        return 1
    print(f"typecheck: {len(current)} recorded error(s), {len(recorded - current)} cleared, no new errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
