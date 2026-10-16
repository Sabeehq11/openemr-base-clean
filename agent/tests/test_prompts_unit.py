"""What the model is allowed to see. Guards against: dropping drug names (a field called `name` is not always PHI),
and leaking the patient's own name or birth date."""
import json
from datetime import date, datetime, timezone

from app.prompts import model_view
from contracts import Medication, PatientSummary, ToolEnvelope, ToolName


def envelope(tool, records):
    return ToolEnvelope(tool=tool, patient_uuid="p-1", retrieved_at=datetime.now(timezone.utc), records=records,
                        empty=False, source_ids=[r.source_id for r in records])


def test_medication_names_reach_the_model():
    med = Medication(source_id="MedicationRequest/m1", name="Metformin", dose_text="500 mg twice daily",
                     status="active", authored_on=None)
    seen = json.loads(model_view(envelope(ToolName.GET_MEDICATIONS, [med])))["records"][0]
    assert seen["name"] == "Metformin" and seen["dose_text"] == "500 mg twice daily"
    assert seen["source_id"] == "MedicationRequest/m1"


def test_patient_name_and_birth_date_never_reach_the_model():
    pt = PatientSummary(source_id="Patient/p-1", patient_uuid="p-1", name="Real Name", birth_date=date(1970, 1, 1),
                        sex="female", age_years=56)
    raw = model_view(envelope(ToolName.GET_PATIENT_SUMMARY, [pt]))
    seen = json.loads(raw)["records"][0]
    assert "name" not in seen and "birth_date" not in seen
    assert "Real Name" not in raw and "1970-01-01" not in raw


def test_patient_uuid_is_never_in_the_model_view():
    med = Medication(source_id="MedicationRequest/m1", name="Metformin", dose_text=None, status="active",
                     authored_on=None)
    assert "p-1" not in model_view(envelope(ToolName.GET_MEDICATIONS, [med]))


# --- the placeholder the model is told to use must never reach the physician ---

from app.prompts import PATIENT_PLACEHOLDER, SYSTEM_PROMPT, restore_patient_reference  # noqa: E402
from contracts import ClaimKind, VerifiedAnswer, VerifiedClaim  # noqa: E402


def answer_with(*texts, notes=()):
    return VerifiedAnswer(claims=[VerifiedClaim(text=t, source_ids=["Patient/p-1"], kind=ClaimKind.FACT) for t in texts],
                          notes=list(notes))


def summary_env(name="Real Name"):
    pt = PatientSummary(source_id="Patient/p-1", patient_uuid="p-1", name=name, birth_date=date(1970, 1, 1),
                        sex="female", age_years=56)
    return envelope(ToolName.GET_PATIENT_SUMMARY, [pt])


def test_placeholder_is_replaced_with_the_patient_name_for_the_physician():
    out = restore_patient_reference(answer_with("[PATIENT] is a 56 year old female", "Seen by [patient] on 2026-01-01"),
                                    [summary_env()])
    assert [c.text for c in out.claims] == ["Real Name is a 56 year old female", "Seen by Real Name on 2026-01-01"]
    assert all("[PATIENT]" not in c.text.upper() for c in out.claims)


def test_placeholder_never_survives_without_a_patient_record():
    out = restore_patient_reference(answer_with("[PATIENT] is a 56 year old female", "Chart of [PATIENT]"), [])
    assert [c.text for c in out.claims] == ["The patient is a 56 year old female", "Chart of the patient"]


def test_placeholder_falls_back_when_the_chart_has_no_name():
    out = restore_patient_reference(answer_with("[PATIENT] is female"), [summary_env(name=None)])
    assert out.claims[0].text == "The patient is female"


def test_restoring_leaves_sources_kinds_and_other_text_untouched():
    out = restore_patient_reference(answer_with("Metformin 500 mg", notes=["note about [PATIENT]"]), [summary_env()])
    assert out.claims[0].text == "Metformin 500 mg" and out.claims[0].source_ids == ["Patient/p-1"]
    assert out.claims[0].kind is ClaimKind.FACT and out.notes == ["note about Real Name"]


def test_physician_facing_text_decodes_html_entities_without_changing_the_claim():
    encoded = "penicillin " + "&" + "amp; sulfa"
    out = restore_patient_reference(answer_with("[PATIENT] takes " + encoded, "HbA1c is 7.9 %"),
                                    [summary_env(name="Ada " + "&" + "amp; Co")])
    assert out.claims[0].text == "Ada & Co takes penicillin & sulfa"
    assert out.claims[1].text == "HbA1c is 7.9 %"
    assert out.claims[0].source_ids == ["Patient/p-1"]


def test_system_prompt_still_tells_the_model_to_use_the_placeholder_not_a_name():
    assert PATIENT_PLACEHOLDER in SYSTEM_PROMPT and "{placeholder}" not in SYSTEM_PROMPT


def test_prompt_tells_the_model_to_decline_plainly_and_stay_with_the_chart():
    from app.prompts import SYSTEM_PROMPT
    flat = " ".join(SYSTEM_PROMPT.split())
    assert "cannot give recommendations" in flat and "only report the chart" in flat


def test_prompt_grounds_medication_concerns_in_records_and_invents_nothing():
    assert "Never state a drug interaction, contraindication or risk from general knowledge" in SYSTEM_PROMPT
    assert "only concerns that a cited record itself shows" in SYSTEM_PROMPT
    assert "Missing evidence is reported by the system" in SYSTEM_PROMPT


def test_prompt_still_forbids_recommending_doses_and_carries_no_dose_value():
    assert "do not recommend doses" in SYSTEM_PROMPT
    assert not any(unit in SYSTEM_PROMPT.lower() for unit in (" mg", " mcg"))
