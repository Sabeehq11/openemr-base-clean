"""A LOINC code is kept only when the document supports it. Each test names the failure it guards."""
import json

import pytest

from app.w2.extract import extract_lab
from app.w2.loinc import REVIEWED, supported_loinc
from tests.w2_helpers import fxt

LDL = "LDL Cholesterol"


@pytest.mark.parametrize("code,name,quote", [
    ("4548-4", "Hemoglobin A1c", "Hemoglobin A1c 7.9 %"),
    ("2160-0", "Creatinine", "Creatinine 1.8 mg/dL"),
    ("2345-7", "Glucose", "Glucose 142 mg/dL"),
    ("2823-3", "Potassium", "Potassium 4.1 mmol/L"),
    ("2089-1", LDL, "LDL Cholesterol 96 mg/dL"),
    ("13457-7", "LDL Cholesterol (calculated)", "LDL Cholesterol (calculated) 96 mg/dL"),
    ("18262-6", "LDL Cholesterol, direct", "LDL Cholesterol, direct 96 mg/dL"),
])
def test_a_code_that_fits_the_printed_test_is_kept(code, name, quote):
    assert supported_loinc(code, name, quote) == code


def test_ldl_by_calculation_is_not_claimed_when_the_page_does_not_say_so():
    """The live model wrote 18262-6 (direct assay) and the stub fixture wrote 13457-7 (calculated) for the same
    printed line 'LDL Cholesterol 96 mg/dL'. Neither method is printed, so neither code is supported."""
    assert supported_loinc("13457-7", LDL, "LDL Cholesterol   96 mg/dL   0-100") is None
    assert supported_loinc("18262-6", LDL, "LDL Cholesterol   96 mg/dL   0-100") is None


def test_the_method_neutral_ldl_code_is_the_one_a_plain_ldl_line_supports():
    assert supported_loinc("2089-1", LDL, "LDL Cholesterol   96 mg/dL   0-100") == "2089-1"


def test_the_two_methods_are_not_interchangeable():
    assert supported_loinc("13457-7", "LDL Cholesterol", "LDL Cholesterol direct 96 mg/dL") is None
    assert supported_loinc("18262-6", "LDL Cholesterol", "LDL Cholesterol calculated 96 mg/dL") is None


@pytest.mark.parametrize("code,name", [
    ("2160-0", "Hemoglobin A1c"),            # real code, wrong test
    ("4548-4", "Creatinine"),
    ("2160-0", "Creatinine clearance"),      # a different test with a similar name
    ("2160-0", "Urine creatinine"),          # a different specimen
    ("2823-3", "Potassium, urine"),
    ("2345-7", "Glucose, fasting"),
])
def test_a_code_for_a_different_test_or_specimen_is_dropped(code, name):
    assert supported_loinc(code, name, f"{name} 1.0") is None


@pytest.mark.parametrize("code", ["9999-9", "1234-5", "not-a-code", "", None, "2160-00", "../2160-0"])
def test_a_code_not_in_the_reviewed_table_is_dropped(code):
    assert supported_loinc(code, "Creatinine", "Creatinine 1.8 mg/dL") is None


def test_dropping_a_code_never_substitutes_another():
    assert supported_loinc("2160-0", "Potassium", "Potassium 4.1") is None


def test_every_reviewed_code_has_a_recorded_meaning():
    assert all(entry.meaning for entry in REVIEWED.values()) and len(REVIEWED) == 7


def raw_with(loinc, name="Hemoglobin A1c", quote="Hemoglobin A1c  7.9 %  4.0-5.6  H"):
    raw = json.loads(fxt("vlm_lab_good.json"))
    raw["rows"][0].update(loinc=loinc, test_name=name)
    raw["rows"][0]["evidence"]["quote"] = quote
    return json.dumps(raw)


def test_extractor_keeps_a_supported_code_and_drops_an_unsupported_one():
    assert extract_lab(raw_with("4548-4"), 7, "Hemoglobin A1c   7.9 %   4.0-5.6   H").rows[0].loinc == "4548-4"
    assert extract_lab(raw_with("2160-0"), 7, "Hemoglobin A1c   7.9 %   4.0-5.6   H").rows[0].loinc is None


def test_the_row_survives_when_only_its_code_is_dropped():
    row = extract_lab(raw_with("9999-9"), 7, "Hemoglobin A1c   7.9 %   4.0-5.6   H").rows[0]
    assert row.loinc is None and row.value == "7.9" and row.support.value == "supported" and row.citation


def test_an_unsupported_row_carries_no_code_at_all():
    rows = extract_lab(fxt("vlm_lab_unsupported.json"), 7).rows
    assert all(r.loinc is None for r in rows if r.support.value == "unsupported")


def test_ldl_in_the_good_fixture_no_longer_carries_a_method_it_does_not_print():
    ldl = [r for r in extract_lab(fxt("vlm_lab_good.json"), 7).rows if r.test_name == LDL][0]
    assert ldl.loinc is None  # fixture says 13457-7 (calculated); its quote does not say calculated
