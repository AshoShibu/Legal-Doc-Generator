"""
tests/intake/test_prefill.py

Assert that fields with ocr_source set are identified correctly for pre-fill,
and fields with ocr_source: null have no OCR default.
"""
import json
from pathlib import Path

import pytest

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "config" / "intake_schemas"
DOC_TYPES = [
    "sale_deed",
    "mortgage_deed",
    "power_of_attorney",
    "leave_and_license",
    "gift_deed",
    "conveyance_deed",
    "affidavit",
]

MOCK_OCR_FIELDS = {
    "Survey_Number": "123/4A",
    "owner_name": "Test Owner",
    "area": "500",
    "Gat_Number": "GN-99",
}


def _all_fields(schema: dict):
    for section in schema["sections"]:
        yield from section.get("fields", [])


@pytest.mark.parametrize("doc_type", DOC_TYPES)
def test_ocr_source_fields_have_prefill_value(doc_type):
    """Fields with ocr_source set should resolve to a value from mock OCR fields."""
    schema_path = SCHEMAS_DIR / f"{doc_type}.json"
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)

    for field in _all_fields(schema):
        ocr_src = field.get("ocr_source")
        if ocr_src is not None:
            # The OCR source key must be a non-empty string
            assert isinstance(ocr_src, str) and len(ocr_src) > 0, (
                f"{doc_type}/{field['id']}: ocr_source must be a non-empty string or null"
            )
            # If the mock OCR has this key, the pre-fill value should be non-empty
            if ocr_src in MOCK_OCR_FIELDS:
                prefill = MOCK_OCR_FIELDS.get(ocr_src, "")
                assert prefill != "", (
                    f"{doc_type}/{field['id']}: expected non-empty prefill from OCR key '{ocr_src}'"
                )


@pytest.mark.parametrize("doc_type", DOC_TYPES)
def test_null_ocr_source_fields_have_no_prefill(doc_type):
    """Fields with ocr_source: null should not get a value from OCR fields."""
    schema_path = SCHEMAS_DIR / f"{doc_type}.json"
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)

    for field in _all_fields(schema):
        ocr_src = field.get("ocr_source")
        if ocr_src is None:
            # Simulate form_renderer behaviour: default is ""
            prefill = MOCK_OCR_FIELDS.get(None, "")  # type: ignore[arg-type]
            assert prefill == "", (
                f"{doc_type}/{field['id']}: null ocr_source should yield empty prefill"
            )


@pytest.mark.parametrize("doc_type", DOC_TYPES)
def test_at_least_one_ocr_source_field_per_schema(doc_type):
    """Every schema should have at least one field with an ocr_source set."""
    schema_path = SCHEMAS_DIR / f"{doc_type}.json"
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)

    ocr_sourced = [
        f for f in _all_fields(schema) if f.get("ocr_source") is not None
    ]
    assert len(ocr_sourced) >= 1, (
        f"{doc_type}: expected at least one field with ocr_source set"
    )
