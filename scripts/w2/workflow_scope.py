#!/usr/bin/env python3
"""The Week 2 gate must run for every surface a Week 2 pull request can change.

A paths filter that lists only agent/ and evals/ lets a module, UI or OpenAPI change merge
without the gate. This check fails if that filter comes back.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "w2-eval-gate.yml"
REQUIRED = (
    "interface/modules/custom_modules/oe-module-copilot/**",
    "docs/week2/openapi.yaml",
    "tests/playwright/specs/w2-*.spec.ts",
    "tests/js/copilot-*.js",
    "tests/Tests/Isolated/Modules/Copilot/**",
)


def main() -> int:
    # PyYAML reads the unquoted key "on" as boolean True.
    doc = yaml.safe_load(WORKFLOW.read_text())
    triggers = doc[True] if True in doc else doc["on"]
    missing = []
    for event in ("push", "pull_request"):
        paths = triggers[event].get("paths")
        if paths is None:
            continue  # no filter: every change runs the gate
        absent = [p for p in REQUIRED if p not in paths]
        if absent:
            missing.append(f"{event} omits {', '.join(absent)}")
    if missing:
        print("workflow scope: a Week 2 change can skip the gate")
        print("\n".join(f"- {m}" for m in missing))
        return 1
    print("workflow scope: module, UI and OpenAPI changes run the gate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
