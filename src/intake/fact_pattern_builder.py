"""
src/intake/fact_pattern_builder.py — Merges OCR fields + intake answers into
an enriched fact pattern dict ready for LLM prompt injection.

Responsibilities:
  - Merge ocr_result.fields and intake_answers under "ocr_fields" / "intake" keys
  - Format number fields representing Indian currency amounts
  - Format date fields as "20 March 2026"
  - Include document_type and generated_at at the top level

Requirements: 4.1
"""
from __future__ import annotations

import datetime
import math
from typing import Any


# ---------------------------------------------------------------------------
# Indian currency formatter
# ---------------------------------------------------------------------------

_ONES = [
    "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
    "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
    "Seventeen", "Eighteen", "Nineteen",
]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _words_below_hundred(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens = _TENS[n // 10]
    ones = _ONES[n % 10]
    return (tens + ("-" + ones if ones else "")).strip()


def _amount_in_words(amount: int) -> str:
    """Convert an integer rupee amount to Indian English words."""
    if amount == 0:
        return "Zero"

    parts: list[str] = []
    crore = amount // 10_000_000
    remainder = amount % 10_000_000
    lakh = remainder // 100_000
    remainder = remainder % 100_000
    thousand = remainder // 1_000
    remainder = remainder % 1_000
    hundred = remainder // 100
    below_hundred = remainder % 100

    if crore:
        parts.append(f"{_words_below_hundred(crore)} Crore{'s' if crore > 1 else ''}")
    if lakh:
        parts.append(f"{_words_below_hundred(lakh)} Lakh{'s' if lakh > 1 else ''}")
    if thousand:
        parts.append(f"{_words_below_hundred(thousand)} Thousand")
    if hundred:
        parts.append(f"{_ONES[hundred]} Hundred")
    if below_hundred:
        parts.append(_words_below_hundred(below_hundred))

    return " ".join(parts)


def _format_indian_currency(amount: float) -> str:
    """
    Format a numeric amount as Indian currency string.
    e.g. 4500000 → "₹45,00,000 (Rupees Forty-Five Lakhs only)"
    """
    int_amount = int(round(amount))

    # Indian number grouping: last 3 digits, then groups of 2
    s = str(int_amount)
    if len(s) <= 3:
        formatted = s
    else:
        # Last 3 digits
        last3 = s[-3:]
        rest = s[:-3]
        # Group rest in pairs from right
        groups = []
        while len(rest) > 2:
            groups.append(rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.append(rest)
        groups.reverse()
        formatted = ",".join(groups) + "," + last3

    words = _amount_in_words(int_amount)
    return f"₹{formatted} (Rupees {words} only)"


# ---------------------------------------------------------------------------
# Date formatter
# ---------------------------------------------------------------------------

def _format_date(value: Any) -> str:
    """
    Format a date value as "20 March 2026" (no leading zero on day).
    Accepts datetime.date, datetime.datetime, or ISO string "YYYY-MM-DD".
    """
    if isinstance(value, datetime.datetime):
        d = value.date()
    elif isinstance(value, datetime.date):
        d = value
    elif isinstance(value, str):
        try:
            d = datetime.date.fromisoformat(value)
        except ValueError:
            return str(value)
    else:
        return str(value)

    return f"{d.day} {d.strftime('%B')} {d.year}"


# ---------------------------------------------------------------------------
# Currency field detection
# ---------------------------------------------------------------------------

_CURRENCY_FIELD_SUFFIXES = (
    "_amount", "_fee", "_deposit", "_paid", "_emi", "_loan",
)

_CURRENCY_FIELD_NAMES = {
    "consideration_amount", "loan_amount", "monthly_license_fee",
    "security_deposit", "emi_amount", "advance_paid", "stamp_duty_paid",
}


def _is_currency_field(field_id: str, schema: dict) -> bool:
    """Heuristic: detect if a field represents an Indian currency amount."""
    if field_id in _CURRENCY_FIELD_NAMES:
        return True
    for suffix in _CURRENCY_FIELD_SUFFIXES:
        if field_id.endswith(suffix):
            return True
    # Check schema field type and label for currency hints
    for section in schema.get("sections", []):
        for field in section.get("fields", []):
            if field["id"] == field_id and field.get("type") == "number":
                label = field.get("label", "").lower()
                if "₹" in label or "amount" in label or "fee" in label or "deposit" in label:
                    return True
    return False


def _is_date_field(field_id: str, schema: dict) -> bool:
    """Detect if a field is a date type."""
    for section in schema.get("sections", []):
        for field in section.get("fields", []):
            if field["id"] == field_id and field.get("type") == "date":
                return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build(
    schema: dict,
    ocr_result: Any,
    intake_answers: dict,
    redaction_result: Any | None = None,
) -> dict:
    """
    Merge OCR fields + intake answers into an enriched fact pattern dict.

    - ocr_result.fields are stored under "ocr_fields"
    - intake_answers are stored under "intake" with formatting applied
    - Number fields representing currency are formatted as Indian currency strings
    - Date fields are formatted as "20 March 2026"
    - document_type and generated_at are included at the top level

    Args:
        schema:           Parsed intake schema dict.
        ocr_result:       OCRResult from src.ocr.pipeline (has .fields dict).
        intake_answers:   Dict of answers from the intake form.
        redaction_result: Optional RedactionResult (reserved for future PII pass-through).

    Returns:
        Structured dict ready for LLM prompt injection.
    """
    import datetime as _dt

    ocr_fields: dict = {}
    if ocr_result is not None and hasattr(ocr_result, "fields"):
        ocr_fields = dict(ocr_result.fields)

    formatted_intake: dict = {}
    for field_id, value in intake_answers.items():
        if value is None or value == "" or value == []:
            continue

        if _is_currency_field(field_id, schema) and isinstance(value, (int, float)):
            formatted_intake[field_id] = _format_indian_currency(value)
        elif _is_date_field(field_id, schema):
            formatted_intake[field_id] = _format_date(value)
        else:
            formatted_intake[field_id] = value

    return {
        "document_type": schema.get("document_type", ""),
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "ocr_fields": ocr_fields,
        "intake": formatted_intake,
    }
