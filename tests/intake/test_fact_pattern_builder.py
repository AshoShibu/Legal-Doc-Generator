"""
tests/intake/test_fact_pattern_builder.py

Assert merge logic, Indian currency formatting, and date formatting.
"""
import datetime
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.intake.fact_pattern_builder import (
    _format_date,
    _format_indian_currency,
    build,
)

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "config" / "intake_schemas"


def _load_schema(doc_type: str) -> dict:
    with open(SCHEMAS_DIR / f"{doc_type}.json", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Currency formatting
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("amount,expected", [
    (4500000, "₹45,00,000 (Rupees Forty-Five Lakhs only)"),
    (450000, "₹4,50,000 (Rupees Four Lakhs Fifty Thousand only)"),
    (1000, "₹1,000 (Rupees One Thousand only)"),
    (100, "₹100 (Rupees One Hundred only)"),
    (10000000, "₹1,00,00,000 (Rupees One Crore only)"),
    (0, "₹0 (Rupees Zero only)"),
    (500, "₹500 (Rupees Five Hundred only)"),
])
def test_format_indian_currency(amount, expected):
    assert _format_indian_currency(amount) == expected


# ---------------------------------------------------------------------------
# Date formatting
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (datetime.date(2026, 3, 20), "20 March 2026"),
    (datetime.date(2026, 1, 1), "1 January 2026"),
    ("2026-03-20", "20 March 2026"),
    ("2025-12-31", "31 December 2025"),
    (datetime.datetime(2026, 3, 20, 10, 30), "20 March 2026"),
])
def test_format_date(value, expected):
    assert _format_date(value) == expected


# ---------------------------------------------------------------------------
# build() — structure
# ---------------------------------------------------------------------------

def test_build_returns_correct_top_level_keys():
    schema = _load_schema("sale_deed")
    ocr_result = MagicMock()
    ocr_result.fields = {"Survey_Number": "123/4A", "owner_name": "Test Owner", "area": "500"}

    intake_answers = {
        "seller_name": "Test Owner",
        "buyer_name": "Buyer Person",
        "consideration_amount": 4500000,
        "execution_date": datetime.date(2026, 3, 20),
    }

    result = build(schema, ocr_result, intake_answers)

    assert "document_type" in result
    assert "generated_at" in result
    assert "ocr_fields" in result
    assert "intake" in result
    assert result["document_type"] == "sale_deed"


def test_build_merges_ocr_fields():
    schema = _load_schema("sale_deed")
    ocr_result = MagicMock()
    ocr_result.fields = {"Survey_Number": "123/4A", "owner_name": "Test Owner"}

    result = build(schema, ocr_result, {})

    assert result["ocr_fields"]["Survey_Number"] == "123/4A"
    assert result["ocr_fields"]["owner_name"] == "Test Owner"


def test_build_formats_currency_in_intake():
    schema = _load_schema("sale_deed")
    ocr_result = MagicMock()
    ocr_result.fields = {}

    intake_answers = {"consideration_amount": 4500000}
    result = build(schema, ocr_result, intake_answers)

    assert result["intake"]["consideration_amount"] == "₹45,00,000 (Rupees Forty-Five Lakhs only)"


def test_build_formats_date_in_intake():
    schema = _load_schema("sale_deed")
    ocr_result = MagicMock()
    ocr_result.fields = {}

    intake_answers = {"execution_date": datetime.date(2026, 3, 20)}
    result = build(schema, ocr_result, intake_answers)

    assert result["intake"]["execution_date"] == "20 March 2026"


def test_build_excludes_empty_intake_values():
    schema = _load_schema("sale_deed")
    ocr_result = MagicMock()
    ocr_result.fields = {}

    intake_answers = {"seller_name": "", "buyer_name": "Buyer"}
    result = build(schema, ocr_result, intake_answers)

    assert "seller_name" not in result["intake"]
    assert result["intake"]["buyer_name"] == "Buyer"


def test_build_handles_none_ocr_result():
    schema = _load_schema("affidavit")
    result = build(schema, None, {"statement_1": "I am the owner."})

    assert result["ocr_fields"] == {}
    assert result["intake"]["statement_1"] == "I am the owner."
