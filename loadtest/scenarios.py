"""Scenario mix tied to USERS.md use cases. Each step is (use_case, question, new_conversation)."""
from __future__ import annotations

import random

BRIEF = "Give me a pre-visit brief: problems, medications, allergies and recent labs."
CHANGED = "What changed since the last visit?"
SAFETY = "Any abnormal labs or medication safety flags?"
FOLLOWUP = "What is the most recent lab result and its date?"
DECLINE = "What dose should I raise the medication to?"

# (weight, steps). UC-4 is a brief followed by a follow-up in the same conversation.
MIX = [
    (45, [("UC-1", BRIEF, True)]),
    (20, [("UC-2", CHANGED, True)]),
    (20, [("UC-3", SAFETY, True)]),
    (10, [("UC-1", BRIEF, True), ("UC-4", FOLLOWUP, False)]),
    (3, [("declined", DECLINE, True)]),
    (2, [("denied", BRIEF, True)]),  # patient of the other physician
]


def pick(rng: random.Random) -> list[tuple[str, str, bool]]:
    return rng.choices([s for _, s in MIX], weights=[w for w, _ in MIX])[0]


# Calibration: a fixed rotation over the four use cases, no declines or denials (those cost nothing and teach nothing).
CALIBRATION = [
    [("UC-1", BRIEF, True)],
    [("UC-2", CHANGED, True)],
    [("UC-3", SAFETY, True)],
    [("UC-1", BRIEF, True), ("UC-4", FOLLOWUP, False)],
]
