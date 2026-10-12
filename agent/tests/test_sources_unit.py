"""Citation cards: what a clicked chip shows. Guards against: a chip with nothing behind it, cards for records nobody
cited, a card inventing or altering a value, and any patient identifier riding along in the card."""
import json
from datetime import date, datetime, timezone

from app.sources import all_cards, attach_sources, cards_for
from contracts import (Allergy, ChangeItem, ClaimKind, Encounter, LabResult, Medication, PatientSummary, Problem,
                       ToolEnvelope, ToolName, VerifiedAnswer, VerifiedClaim)

NAME, DOB, PUUID = "Real Name", date(1970, 1, 1), "p-secret-uuid"
MED = Medication(source_id="MedicationRequest/m1", name="Metformin", dose_text="500 mg twice daily", status="active",
                 authored_on=date(2026, 1, 10))
LAB = LabResult(source_id="Observation/o1", test="Hemoglobin A1c", loinc="4548-4", value=8.2, unit="%", ref_low=4.0,
                ref_high=5.6, flag_reported="H", collected=date(2026, 9, 1))
PROB = Problem(source_id="Condition/c1", title="Type 2 diabetes mellitus", code="E11.9", code_system="ICD-10",
               status="active", onset=date(2019, 3, 2))
ALG = Allergy(source_id="AllergyIntolerance/a1", substance="Penicillin", status="active", reaction="rash",
              criticality=None)
PT = PatientSummary(source_id="Patient/p1", patient_uuid=PUUID, name=NAME, birth_date=DOB, sex="female", age_years=56)


def env(tool, *records):
    return ToolEnvelope(tool=tool, patient_uuid=PUUID, retrieved_at=datetime.now(timezone.utc), records=list(records),
                        empty=not records, source_ids=[r.source_id for r in records])


ENVS = [env(ToolName.GET_MEDICATIONS, MED), env(ToolName.GET_LABS, LAB), env(ToolName.GET_PROBLEMS, PROB),
        env(ToolName.GET_ALLERGIES, ALG), env(ToolName.GET_PATIENT_SUMMARY, PT)]


def fields(card):
    return {f.label: f.value for f in card.fields}


def test_medication_card_shows_drug_and_verbatim_dose():
    (card,) = cards_for(["MedicationRequest/m1"], ENVS)
    assert card.title == "Metformin" and card.kind == "Medication"
    assert fields(card)["Dose"] == "500 mg twice daily" and fields(card)["Status"] == "active"


def test_lab_card_shows_value_range_and_the_stored_flag_unaltered():
    (card,) = cards_for(["Observation/o1"], ENVS)
    f = fields(card)
    assert card.title == "Hemoglobin A1c" and f["Value"] == "8.2 %" and f["Reference range"] == "4 to 5.6"
    assert f["Flag stored in chart"] == "H" and f["Collected"] == "2026-09-01"


def test_problem_and_allergy_cards():
    prob, alg = cards_for(["Condition/c1", "AllergyIntolerance/a1"], ENVS)
    assert prob.title == "Type 2 diabetes mellitus" and fields(prob)["Code"] == "E11.9 (ICD-10)"
    assert alg.title == "Penicillin" and fields(alg)["Reaction"] == "rash" and "Criticality" not in fields(alg)


def test_cards_follow_citation_order_and_are_deduplicated():
    cards = cards_for(["Observation/o1", "MedicationRequest/m1", "Observation/o1"], ENVS)
    assert [c.source_id for c in cards] == ["Observation/o1", "MedicationRequest/m1"]


def test_an_id_with_no_fetched_record_gets_no_card():
    assert cards_for(["MedicationRequest/never-fetched"], ENVS) == []


def test_patient_card_has_age_and_sex_but_never_identifiers():
    (card,) = cards_for(["Patient/p1"], ENVS)
    blob = card.model_dump_json()
    assert fields(card) == {"Age": "56", "Sex": "female"}
    assert NAME not in blob and "1970" not in blob and PUUID not in blob


def test_no_card_anywhere_carries_a_patient_identifier():
    blob = json.dumps([c.model_dump() for c in all_cards(ENVS)])
    assert NAME not in blob and "1970-01-01" not in blob and PUUID not in blob


def test_attach_sources_covers_exactly_the_cited_ids():
    ans = VerifiedAnswer(claims=[
        VerifiedClaim(text="Metformin", source_ids=["MedicationRequest/m1"], kind=ClaimKind.FACT),
        VerifiedClaim(text="Not documented", source_ids=[], kind=ClaimKind.ABSENCE)])
    out = attach_sources(ans, ENVS)
    assert [c.source_id for c in out.sources] == ["MedicationRequest/m1"]  # the lab was fetched but not cited


def test_changes_since_item_does_not_shadow_the_full_record():
    item = ChangeItem(source_id="MedicationRequest/m1", resource_type="MedicationRequest", changed_on=date(2026, 1, 10),
                      summary="Medication on record: Metformin 500 mg twice daily")
    changes = env(ToolName.GET_CHANGES_SINCE, item)
    for order in ([changes, ENVS[0]], [ENVS[0], changes]):
        (card,) = cards_for(["MedicationRequest/m1"], order)
        assert card.kind == "Medication" and fields(card)["Dose"] == "500 mg twice daily"


def test_changes_since_item_alone_still_gets_a_card():
    item = ChangeItem(source_id="Encounter/e1", resource_type="Encounter", changed_on=date(2026, 9, 1), summary="Visit: Follow-up")
    (card,) = cards_for(["Encounter/e1"], [env(ToolName.GET_CHANGES_SINCE, item)])
    assert card.title == "Visit: Follow-up" and fields(card)["Date"] == "2026-09-01"


def test_encounter_card():
    enc = Encounter(source_id="Encounter/e2", date=date(2026, 9, 1), reason="Follow-up", kind="office")
    (card,) = cards_for(["Encounter/e2"], [env(ToolName.GET_CHANGES_SINCE, enc)])
    assert card.title == "Follow-up" and fields(card) == {"Date": "2026-09-01", "Type": "office"}
