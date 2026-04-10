"""
tests/intake/test_schema_validation.py

Assert all 7 intake schemas load without error and have the required structure.
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


@pytest.mark.parametrize("doc_type", DOC_TYPES)
def test_schema_loads_without_error(doc_type):
    schema_path = SCHEMAS_DIR / f"{doc_type}.json"
    assert schema_path.exists(), f"Schema file missing: {schema_path}"
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)
    assert isinstance(schema, dict), "Schema must be a dict"


@pytest.mark.parametrize("doc_type", DOC_TYPES)
def test_schema_has_required_top_level_keys(doc_type):
    schema_path = SCHEMAS_DIR / f"{doc_type}.json"
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)
    assert "document_type" in schema, "Missing 'document_type'"
    assert "display_name" in schema, "Missing 'display_name'"
    assert "sections" in schema, "Missing 'sections'"
    assert schema["document_type"] == doc_type


@pytest.mark.parametrize("doc_type", DOC_TYPES)
def test_schema_has_at_least_one_required_field(doc_type):
    schema_path = SCHEMAS_DIR / f"{doc_type}.json"
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)
    required_fields = [
        field
        for section in schema["sections"]
        for field in section.get("fields", [])
        if field.get("required", False)
    ]
    assert len(required_fields) >= 1, f"{doc_type}: no required fields found"


@pytest.mark.parametrize("doc_type", DOC_TYPES)
def test_all_fields_have_mandatory_keys(doc_type):
    schema_path = SCHEMAS_DIR / f"{doc_type}.json"
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)
    for section in schema["sections"]:
        for field in section.get("fields", []):
            for key in ("id", "label", "type", "required"):
                assert key in field, (
                    f"{doc_type}/{section['title']}/{field.get('id', '?')}: "
                    f"missing key '{key}'"
                )


@pytest.mark.parametrize("doc_type", DOC_TYPES)
def test_select_fields_have_options(doc_type):
    schema_path = SCHEMAS_DIR / f"{doc_type}.json"
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)
    for section in schema["sections"]:
        for field in section.get("fields", []):
            if field["type"] in ("select", "multiselect"):
                assert "options" in field and len(field["options"]) > 0, (
                    f"{doc_type}/{field['id']}: select/multiselect field missing options"
                )
