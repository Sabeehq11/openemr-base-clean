"""Advice-request detection. Guards against two opposite failures: missing a request for the assistant's own
judgment (diagnosis, drug, dose), and over-blocking an ordinary chart lookup that happens to contain the same words."""
import pytest

from app.advice import AdviceKind, classify, decline_text

TREATMENT = [
    "What dose should I raise metformin to?",
    "Should I increase the lisinopril?",
    "Should we stop the warfarin?",
    "Which medication should she start for her blood pressure?",
    "Recommend a treatment for this patient",
    "Can you suggest a dose for the atorvastatin?",
    "How much should I prescribe?",
    "Increase metformin to 1000 mg",
    "what drug would you recommend to start",
    "What is the diagnosis and what dose should I raise metformin to?",
    # Found missing in review: the plainest ways to ask for advice.
    "what do you recommend?",
    "what should I do for her?",
    "what would you prescribe?",
    "tell me what to start",
    "Any recommendations?",
    "What's the best treatment?",
    # Borderline, deliberately treated as advice: they ask the assistant to judge safety or suitability.
    "can she take ibuprofen?",
    "is it safe to give aspirin?",
]
DIAGNOSIS = [
    "What is the diagnosis?",
    "What's the differential here?",
    "Can you diagnose this patient?",
    "What does she have?",
    "Could this be an infection?",
    "What could this be?",
    "What's wrong with her?",
    "is she diabetic?",  # borderline, deliberately treated as a request for a conclusion
]
CHART_QUESTIONS = [
    "Give me a pre-visit brief for this patient.",
    "What medications is the patient on?",
    "What dose of metformin is she on?",
    "What is the current dose of lisinopril?",
    "List the active problems.",
    "What was the last A1c?",
    "Any allergies documented?",
    "What changed since 2026-01-01?",
    "When was the medication last prescribed?",
    "Which problems have a diagnosis code recorded?",
    # Found over-blocked in review: same words as advice, but they ask for a recorded fact.
    "What does the patient have documented for allergies?",
    "What is her diagnosis code for Condition c1?",
    "How many refills should be on file?",
    "Should I stop by room 3?",
    "Did they increase the dose to 20 mg?",
    # More recorded-fact and history phrasing around the same verbs.
    "Was metformin raised to 1000 mg last year?",
    "Has the lisinopril dose been changed?",
    "What medication changes happened since the last visit?",
    "What was the dose increased to in March?",
    "What did the last note say about stopping aspirin?",
    "Which medications did we stop in 2025?",
    "What treatment is she currently on?",
    "What therapy did she start in 2025?",
    "Show me dose changes since January",
    "Is there a diagnosis of diabetes in the chart?",
    "What is the diagnosis date for hypertension?",
    "Which problems does she have?",
    "Is she allergic?",
    "Is she on any blood thinners?",
    "Should I be concerned about the A1c trend?",
    "Should we review her labs?",
    "What should I know before the visit?",
    "What is the next appointment?",
]


@pytest.mark.parametrize("q", TREATMENT)
def test_treatment_requests_are_detected(q):
    assert classify(q) is AdviceKind.TREATMENT


@pytest.mark.parametrize("q", DIAGNOSIS)
def test_diagnosis_requests_are_detected(q):
    assert classify(q) is AdviceKind.DIAGNOSIS


@pytest.mark.parametrize("q", CHART_QUESTIONS)
def test_ordinary_chart_questions_are_not_blocked(q):
    assert classify(q) is None


@pytest.mark.parametrize("q", ["WHAT DO YOU RECOMMEND?", "  what dose SHOULD i raise metformin to  "])
def test_matching_ignores_case_and_spacing(q):
    assert classify(q) is AdviceKind.TREATMENT


def test_treatment_wins_when_both_are_asked():
    assert classify("What is the diagnosis and what dose should I raise metformin to?") is AdviceKind.TREATMENT


def test_decline_text_says_no_and_does_not_restate_chart_content():
    for kind in AdviceKind:
        t = decline_text(kind)
        assert t.startswith("I can't") and "your" in t
        assert not any(ch.isdigit() for ch in t)  # no values, doses or dates
