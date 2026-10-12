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
_TREATMENT = [
    re.compile(rf"\bshould\s+{_SUBJ}\s+{_ACT}\b", _I),
    re.compile(rf"\b(?:what|which)\b[^.?]{{0,25}}\b{_DRUGWORD}\b[^.?]{{0,30}}"
               rf"\b(?:should\s+{_SUBJ}|would you|do you (?:recommend|suggest)|to (?:start|give|use|prescribe))\b", _I),
    re.compile(r"\bwhat (?:would|do|can) you (?:prescribe|recommend|suggest|start|give|advise)\b", _I),
    re.compile(r"\b(?:do you|would you|can you) (?:recommend|suggest|advise)\b", _I),
    re.compile(r"\b(?:any|your|a) (?:recommendations?|suggestions?|advice)\b", _I),
    re.compile(r"\b(?:recommend|suggest)\w*\s+(?:a|an|the|some|any)?\s*(?:\w+\s+)?" + _DRUGWORD + r"\b", _I),
    re.compile(r"\bwhat\s+(?:should|shall)\s+(?:i|we)\s+(?:do|start|give|prescribe|order|try)\b", _I),
    re.compile(r"\btell me what to\s+(?:start|give|prescribe|do|use|try)\b", _I),
    re.compile(r"\bhow (?:much|many)\b[^.?]{0,30}\bshould\s+(?:i|we)\b", _I),
    re.compile(r"\b(?:best|right|next) (?:treatment|therapy|drug|medication|option|step)\b", _I),
    re.compile(r"\b(?:can|could|may)\s+(?:she|he|they|the patient|i|we)\s+(?:safely\s+)?(?:take|start|have|be given|be prescribed)\b", _I),
    re.compile(r"\bis it (?:safe|ok|okay|alright|appropriate)\s+to\s+(?:give|take|start|prescribe|use|stop)\b", _I),
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


_NOTICE = {
    AdviceKind.DIAGNOSIS: "I can't suggest a diagnosis. I only report what is recorded in the chart. "
                          "Diagnosis is your clinical judgment.",
    AdviceKind.TREATMENT: "I can't recommend a drug, a dose or a change to treatment. I only report what is recorded "
                          "in the chart. Treatment decisions are yours.",
}


def decline_text(kind: AdviceKind) -> str:
    return _NOTICE[kind]
