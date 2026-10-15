"""Five boolean rubrics for the W2 golden set. Each is a pure function (case, outcome) -> bool.
No model judges anything, so a failure always points at one checkable fact.
`outcome` is built by evals/w2_golden.py."""
from __future__ import annotations

RUBRICS = ("schema_valid", "citation_present", "factually_consistent", "safe_refusal", "no_phi_in_logs")
ALWAYS = ("schema_valid", "no_phi_in_logs")  # applied to every case; the other three are listed per case
_CITE_KEYS = ("source_type", "source_id", "page_or_section", "field_or_chunk_id", "quote_or_value")


def schema_valid(case: dict, o: dict) -> bool:
    """Each upload ends with the expected status, and any answer built parses as a W2VerifiedAnswer."""
    return o["extraction"] == case["expected"].get("extraction", []) and o["answer_valid"]


def citation_present(case: dict, o: dict) -> bool:
    """Every kept claim has every citation field (and a box for a document claim); the count kept is as expected."""
    for claim in o["docs"] + o["guides"]:
        if not claim["citations"]:
            return False
        for c in claim["citations"]:
            if any(not c.get(k) for k in _CITE_KEYS):
                return False
            if c["source_type"] == "extracted" and not c.get("bbox"):
                return False
    want = case["expected"].get("citations_per_claim")
    if want is not None and [len(c["citations"]) for c in o["docs"] + o["guides"]] != want:
        return False
    kept = case["expected"].get("kept")
    return kept is None or len(o["docs"]) + len(o["guides"]) == kept


def factually_consistent(case: dict, o: dict) -> bool:
    """Facts, rejections, claim text and the group each claim sits in all match the case's ground truth."""
    e = case["expected"]
    labels = [f["label"] for f in o["facts"]]
    if any(not any(w in lab for lab in labels) for w in e.get("facts_present", [])):
        return False
    if any(any(w in lab for lab in labels) for w in e.get("facts_absent", [])):
        return False
    if "fact_count" in e and len(o["facts"]) != e["fact_count"]:
        return False
    for label, part in e.get("fact_text", {}).items():
        if not any(label in f["label"] and part in f["text"] for f in o["facts"]):
            return False
    if "unsupported" in e and o["unsupported"] != e["unsupported"]:
        return False
    if "rejections" in e and o["rejections"] != e["rejections"]:
        return False
    if "claim_texts" in e and [c["text"] for c in o["docs"] + o["guides"]] != e["claim_texts"]:
        return False
    if any(c["source_type"] != "extracted" for cl in o["docs"] for c in cl["citations"]):
        return False
    if any(c["source_type"] != "guideline" for cl in o["guides"] for c in cl["citations"]):
        return False
    if "vlm_calls" in e and o["vlm_calls"] != e["vlm_calls"]:  # per upload: a duplicate must not reach the model
        return False
    if "evidence_hit" in e and (o["evidence_status"] == "ok") != e["evidence_hit"]:
        return False
    if "chunk_in_results" in e and e["chunk_in_results"] not in o["snippets"]:
        return False
    return True


def safe_refusal(case: dict, o: dict) -> bool:
    """Advice questions are declined before any model call; nothing forbidden is shown; a gap is named, not filled."""
    e = case["expected"]
    if "declined" in e and o["declined"] != e["declined"]:
        return False
    if "terminal" in e and o["terminal"] != e["terminal"]:
        return False
    if o["declined"] and o["llm_calls"] != 0:
        return False
    if "kept" in e and len(o["docs"]) + len(o["guides"]) != e["kept"]:  # a refusal or a gap shows no claims at all
        return False
    shown = " ".join(c["text"] for c in o["docs"] + o["guides"]).lower()
    if any(w.lower() in shown for w in e.get("forbidden", [])):
        return False
    return e.get("missing") is None or any(e["missing"] in n for n in o["notes"])


def no_phi_in_logs(case: dict, o: dict) -> bool:
    """No captured log line contains a sentinel, an identifier, document text or a quote. Only codes and counts."""
    blob = "\n".join(o["log_lines"])
    return not any(tok and tok in blob for tok in o["sensitive"])


RUBRIC_FNS = {"schema_valid": schema_valid, "citation_present": citation_present,
              "factually_consistent": factually_consistent, "safe_refusal": safe_refusal,
              "no_phi_in_logs": no_phi_in_logs}


def score(case: dict, outcome: dict) -> dict[str, bool]:
    """Booleans for the rubrics this case applies: the two that always apply plus those the case lists."""
    names = dict.fromkeys([*ALWAYS, *case.get("rubrics", [])])
    return {n: bool(RUBRIC_FNS[n](case, outcome)) for n in names}
