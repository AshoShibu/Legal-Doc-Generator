"""
tests/intake/test_conditional_fields.py

Assert conditional_on logic: fields with a condition are excluded from
answers when the condition is not met.
"""
import json
from pathlib import Path

import pytest

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "config" / "intake_schemas"


def _load_schema(doc_type: str) -> dict:
    with open(SCHEMAS_DIR / f"{doc_type}.json", encoding="utf-8") as f:
        return json.load(f)


def _all_fields(schema: dict):
    for section in schema["sections"]:
        yield from section.get("fields", [])


def _condition_met(field: dict, current_answers: dict) -> bool:
    """Mirror of the old condition helper for schema logic testing."""
    cond = field.get("conditional_on")
    if cond is None:
        return True
    ref_field = cond.get("field")
    ref_value = cond.get("value")
    return current_answers.get(ref_field) == ref_value


# ---------------------------------------------------------------------------
# sale_deed: encumbrance_details only shown when encumbrance == False
# ---------------------------------------------------------------------------

def test_encumbrance_details_hidden_when_encumbrance_true():
    schema = _load_schema("sale_deed")
    conditional_fields = [
        f for f in _all_fields(schema) if f.get("conditional_on") is not None
    ]
    assert len(conditional_fields) >= 1, "Expected at least one conditional field in sale_deed"

    enc_details = next(
        (f for f in conditional_fields if f["id"] == "encumbrance_details"), None
    )
    assert enc_details is not None, "encumbrance_details field not found"

    # When encumbrance is True (property IS free), details should NOT be shown
    answers_free = {"encumbrance": True}
    assert not _condition_met(enc_details, answers_free), (
        "encumbrance_details should be hidden when encumbrance=True"
    )


def test_encumbrance_details_shown_when_encumbrance_false():
    schema = _load_schema("sale_deed")
    enc_details = next(
        (f for f in _all_fields(schema) if f["id"] == "encumbrance_details"), None
    )
    assert enc_details is not None

    # When encumbrance is False (property has encumbrance), details SHOULD be shown
    answers_encumbered = {"encumbrance": False}
    assert _condition_met(enc_details, answers_encumbered), (
        "encumbrance_details should be shown when encumbrance=False"
    )


# ---------------------------------------------------------------------------
# gift_deed: gift_conditions_text only shown when is_conditional_gift == True
# ---------------------------------------------------------------------------

def test_gift_conditions_hidden_when_not_conditional():
    schema = _load_schema("gift_deed")
    cond_text = next(
        (f for f in _all_fields(schema) if f["id"] == "gift_conditions_text"), None
    )
    assert cond_text is not None, "gift_conditions_text field not found"

    assert not _condition_met(cond_text, {"is_conditional_gift": False})
    assert _condition_met(cond_text, {"is_conditional_gift": True})


# ---------------------------------------------------------------------------
# power_of_attorney: expiry_date only shown when is_limited_duration == True
# ---------------------------------------------------------------------------

def test_expiry_date_conditional_on_limited_duration():
    schema = _load_schema("power_of_attorney")
    expiry = next(
        (f for f in _all_fields(schema) if f["id"] == "expiry_date"), None
    )
    assert expiry is not None, "expiry_date field not found"

    assert not _condition_met(expiry, {"is_limited_duration": False})
    assert _condition_met(expiry, {"is_limited_duration": True})


# ---------------------------------------------------------------------------
# affidavit: purpose_other_details only shown when affidavit_purpose == "Other"
# ---------------------------------------------------------------------------

def test_purpose_other_details_conditional():
    schema = _load_schema("affidavit")
    other_details = next(
        (f for f in _all_fields(schema) if f["id"] == "purpose_other_details"), None
    )
    assert other_details is not None, "purpose_other_details field not found"

    assert not _condition_met(other_details, {"affidavit_purpose": "Property Ownership"})
    assert _condition_met(other_details, {"affidavit_purpose": "Other"})


# ---------------------------------------------------------------------------
# Generic: all conditional fields have valid conditional_on structure
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("doc_type", [
    "sale_deed", "mortgage_deed", "power_of_attorney",
    "leave_and_license", "gift_deed", "conveyance_deed", "affidavit",
])
def test_conditional_on_structure_is_valid(doc_type):
    schema = _load_schema(doc_type)
    for field in _all_fields(schema):
        cond = field.get("conditional_on")
        if cond is not None:
            assert "field" in cond, (
                f"{doc_type}/{field['id']}: conditional_on missing 'field' key"
            )
            assert "value" in cond, (
                f"{doc_type}/{field['id']}: conditional_on missing 'value' key"
            )
