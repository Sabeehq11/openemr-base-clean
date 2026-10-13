"""Detects requests for diagnosis or treatment advice, and the notice shown when the assistant declines.

The check is on the physician's question, not the model's output, so the decline does not depend on the model
cooperating. It separates two kinds of question:

  * a chart lookup ("what dose is she on?", "did they increase the dose?", "what is the diagnosis code?") is answered;
  * a request for the assistant's own judgment ("what dose should I raise it to?", "what do you recommend?",
    "is it safe to give aspirin?") is declined.

Advice requests are phrased as should/would/recommend, a direct instruction, or a "can/is it safe to" question.
Past-tense or recorded-fact phrasing ("did", "was", "is on", "documented") never matches.
"""
from __future__ import annotations

import re
from enum import Enum


class AdviceKind(Enum):
    DIAGNOSIS = "diagnosis"
    TREATMENT = "treatment"


_I = re.I
_SUBJ = r"(?:i|we|she|he|they|the patient|this patient)"
_ACT = (r"(?:start|stop(?!\s+by\b)|increase|decrease|raise|lower|change|switch|take|prescribe|titrate|hold|"
        r"discontinue|give|add|adjust|continue|taper)")
_DRUGWORD = r"(?:dose|dosage|dosing|drug|medication|med|treatment|therapy|regimen)"
_DOSEW = r"(?:dose|dosage|dosing)"
_ADJ = r"(?:right|correct|safe|safest|appropriate|best|ideal|optimal|proper|suitable|recommended|target)"
_REQ = r"(?:give|tell|provide|calculate|compute|determine|work out|figure out|recommend|suggest|propose|write|draft)"
# Requests for a specific dose. They do not depend on a pronoun subject ("Maria's warfarin", "the physician", "I"), and
# they need a request word or an "appropriate/right/safe" judgment word, so "what dose is she on?" stays a lookup.
_DOSE_REQUESTS = [
    re.compile(rf"\bhow (?:much|many)\b[^.?]{{0,40}}\b(?:increase|raise|lower|decrease|reduce|adjust|titrate|change|up|down)\b", _I),
    re.compile(r"\bhow (?:much|many)\b[^.?]{0,40}\bshould\s+(?!be\b|have been\b)\w+", _I),
    re.compile(rf"\b{_REQ}\b[^.?]{{0,40}}\b(?:new|specific|exact|right|correct|safe|appropriate|adjusted|target|"
               rf"recommended)\s+(?:[\w']+\s+)?{_DOSEW}\b", _I),
    re.compile(rf"\bhow\s+(?:should|do|would|can|could)\s+(?:i|we)\s+(?:best\s+)?dose\b", _I),
    re.compile(rf"\b{_REQ}\b[^.?]{{0,30}}\b{_DOSEW}\s+(?:recommendation|plan|schedule|regimen|adjustment|suggestion)s?\b", _I),
    re.compile(rf"\bwhat(?:'s| is| would be| will be)?\s+(?:the |her |his |their )?(?:[\w']+\s+){{0,2}}{_ADJ}\s+(?:[\w']+\s+)?{_DOSEW}\b"
               rf"(?![^.?]*\b(?:listed|recorded|documented|on file|in the (?:chart|record|notes?))\b)", _I),
    re.compile(rf"\bwhat\b[^.?]{{0,30}}\b{_DOSEW}\b[^.?]{{0,20}}\b(?:is|would be|will be|be)\s+(?:the\s+)?{_ADJ}\b", _I),
    re.compile(rf"\bwhat\s+should\b[^.?]{{0,30}}\b{_DOSEW}\b[^.?]{{0,15}}\bbe\b", _I),
    re.compile(r"\bis\s+\d+(?:\.\d+)?\s*(?:mg|mcg|units?)\b[^.?]{0,30}\b(?:ok|okay|safe|fine|appropriate|alright|right|too (?:high|low|much))\b", _I),
    re.compile(r"\b(?:increase|raise|lower|decrease|reduce|adjust|titrate)\b[^.?]{0,40}\b(?:to what|by how much|by what)\b", _I),
    re.compile(rf"^\s*(?:please\s+)?dose\b", _I),
    re.compile(rf"^\s*(?:please\s+)?(?:calculate|compute|determine|work out|write|draft|create)\b[^.?]{{0,30}}\b"
               rf"(?:{_DOSEW}|regimen|titration)\b", _I),
]
_TREATMENT = [
    re.compile(rf"\bshould\s+{_SUBJ}\s+{_ACT}\b", _I),
    re.compile(rf"\b(?:what|which)\b[^.?]{{0,25}}\b{_DRUGWORD}\b[^.?]{{0,30}}"
               rf"\b(?:should\s+{_SUBJ}\s+(?:{_ACT}|use|order|try)|would you|do you (?:recommend|suggest)"
               rf"|to (?:start|give|use|prescribe))\b", _I),
    re.compile(r"\bwhat (?:would|do|can) you (?:prescribe|recommend|suggest|start|give|advise)\b", _I),
    re.compile(r"\b(?:do you|would you|can you) (?:recommend|suggest|advise)\b", _I),
    re.compile(r"\b(?:any|your|a) (?:recommendations?|suggestions?|advice)\b", _I),
    # "recommended dose listed in the chart" uses the adjective and asks for a recorded fact, so -ed is excluded.
    re.compile(r"\b(?:recommend|suggest)(?!ed\b)\w*\s+(?:a|an|the|some|any)?\s*(?:\w+\s+)?" + _DRUGWORD + r"\b", _I),
    re.compile(r"\bwhat\s+(?:should|shall)\s+(?:i|we)\s+(?:do|start|give|prescribe|order|try)\b", _I),
    re.compile(r"\btell me what to\s+(?:start|give|prescribe|do|use|try)\b", _I),
    re.compile(r"\bhow (?:much|many)\b[^.?]{0,30}\bshould\s+(?:i|we)\b", _I),
    re.compile(r"\b(?:best|right|next) (?:treatment|therapy|drug|medication|option|step)\b", _I),
    re.compile(r"\b(?:can|could|may)\s+(?:she|he|they|the patient|i|we)\s+(?:safely\s+)?(?:take|start|have|be given|be prescribed)\b", _I),
    re.compile(r"\bis it (?:safe|ok|okay|alright|appropriate)\s+to\s+(?:give|take|start|prescribe|use|stop)\b", _I),
    *_DOSE_REQUESTS,
    # A direct instruction to the assistant. Only at the start, so "did they increase the dose?" never matches.
    re.compile(rf"^\s*(?:please\s+|can you\s+)?(?:increase|decrease|raise|lower|titrate|adjust|change|start|stop|discontinue|"
               rf"hold|prescribe|switch|taper)\b", _I),
]
_END = r"\s*(?:here|now|today|for (?:her|him|them|this patient|the patient))?\s*[?.!]*\s*$"
_DIAGNOSIS = [
    # "what is the diagnosis?" asks for a conclusion; "what is the diagnosis code / date / list" is a chart lookup.
    re.compile(r"\bwhat(?:'s| is| are)\b[^.?]{0,25}\b(?:diagnos[ie]s|differential)" + _END, _I),
    re.compile(r"\b(?:can|could|would|will) you\s+(?:please\s+)?diagnose\b", _I),
    re.compile(r"^\s*(?:please\s+)?diagnose\b", _I),
    re.compile(r"\bwhat (?:does|do) (?:she|he|they|the patient|this patient) have\s*[?.!]*\s*$", _I),
    re.compile(r"\bwhat(?:'s| is) (?:wrong|going on) with (?:her|him|them|the patient|this patient)\b", _I),
    re.compile(r"\bwhat (?:could|might|may|would) (?:this|it|that) be\b", _I),
    re.compile(r"\bwhat (?:is|could be|might be) (?:causing|behind|driving)\b", _I),
    re.compile(r"\b(?:could|might|may) (?:this|it|that|she|he) (?:be|have)\b[^.?]{0,40}\?", _I),
    # A bare "is she diabetic?" asks the assistant to conclude. "Is she allergic?" is left to the chart.
    re.compile(r"\bis (?:she|he|the patient|this patient) (?!allergic\b)\w+(?:ic|ive|emic|ous)\s*[?.!]*\s*$", _I),
]


def classify(question: str) -> AdviceKind | None:
    """Treatment wins over diagnosis when a question asks for both."""
    if any(p.search(question) for p in _TREATMENT):
        return AdviceKind.TREATMENT
    if any(p.search(question) for p in _DIAGNOSIS):
        return AdviceKind.DIAGNOSIS
    return None


_DIAGNOSIS_NOTICE = ("I can't suggest a diagnosis. I only report what is recorded in the chart. "
                     "Diagnosis is your clinical judgment.")


def decline_text(kind: AdviceKind, drugs: tuple[str, ...] = ()) -> str:
    """The refusal shown to the physician. `drugs` are names from retrieved medication records, never from the question,
    so the text cannot echo anything the caller typed. It never states or implies a dose."""
    if kind is AdviceKind.DIAGNOSIS:
        return _DIAGNOSIS_NOTICE
    what = f"a {' or '.join(drugs)} dose" if drugs else "a dose"
    return (f"I can't recommend, calculate or adjust {what}, and I can't recommend starting, stopping or changing a "
            "drug. I won't suggest a new dose. The right dose depends on current lab results, other medications and "
            "the patient's risks, which I can't weigh. I only report what is recorded in the chart. Treatment "
            "decisions are yours.")
