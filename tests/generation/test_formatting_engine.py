"""
tests/generation/test_formatting_engine.py

Unit tests for Formatting_Engine (src/generation/formatting_engine.py).

Covers:
  - detect_party_names: per-doc-type patterns, exclusions
  - detect_operative_clauses: all four operative phrases
  - parse_underline_markers: <UNDERLINE>...</UNDERLINE> parsing
  - get_underline_ranges: marker-based vs heuristic fallback
  - underline_parties_pdf_html: <u> tag injection
"""
from __future__ import annotations

import pytest

from src.generation.formatting_engine import (
    detect_operative_clauses,
    detect_party_names,
    get_underline_ranges,
    parse_underline_markers,
    underline_parties_pdf_html,
)


# ---------------------------------------------------------------------------
# detect_party_names
# ---------------------------------------------------------------------------

class TestDetectPartyNames:
    def test_sale_deed_vendor(self):
        text = "Vendor Shri Rajesh Kumar Sharma, residing at Mumbai."
        hits = detect_party_names(text, "sale_deed")
        assert any("Vendor" in role for _, _, role in hits)

    def test_sale_deed_purchaser(self):
        text = "Purchaser Smt Priya Patel, residing at Pune."
        hits = detect_party_names(text, "sale_deed")
        assert any("Purchaser" in role for _, _, role in hits)

    def test_mortgage_deed_mortgagor(self):
        text = "Mortgagor Shri Anil Desai agrees to repay."
        hits = detect_party_names(text, "mortgage_deed")
        assert any("Mortgagor" in role for _, _, role in hits)

    def test_mortgage_deed_mortgagee(self):
        text = "Mortgagee State Bank of India holds the security."
        hits = detect_party_names(text, "mortgage_deed")
        assert any("Mortgagee" in role for _, _, role in hits)

    def test_power_of_attorney_principal(self):
        text = "Principal Shri Ramesh Joshi grants authority."
        hits = detect_party_names(text, "power_of_attorney")
        assert any("Principal" in role for _, _, role in hits)

    def test_affidavit_deponent(self):
        text = "Deponent Shri Suresh Patil states on oath."
        hits = detect_party_names(text, "affidavit")
        assert any("Deponent" in role for _, _, role in hits)

    def test_gift_deed_donor_donee(self):
        text = "Donor Shri Mohan Rao gifts to Donee Smt Lata Rao."
        hits = detect_party_names(text, "gift_deed")
        roles = [role for _, _, role in hits]
        assert any("Donor" in r for r in roles)
        assert any("Donee" in r for r in roles)

    def test_no_party_names(self):
        text = "This is a general clause with no party names."
        hits = detect_party_names(text, "sale_deed")
        assert hits == []

    def test_returns_sorted_by_offset(self):
        text = "Vendor Shri A Kumar and Purchaser Smt B Patel."
        hits = detect_party_names(text, "sale_deed")
        offsets = [start for start, _, _ in hits]
        assert offsets == sorted(offsets)

    def test_unknown_doc_type_uses_fallback(self):
        text = "Vendor Shri Rajesh Kumar and Mortgagor Shri Anil Desai."
        hits = detect_party_names(text, "unknown_doc_type")
        assert len(hits) > 0  # fallback should still find something

    def test_citation_markers_excluded(self):
        text = "[CITE:Transfer of Property Act, 1882, Section 54] Vendor Shri A Kumar."
        hits = detect_party_names(text, "sale_deed")
        # The [CITE:...] span should not be in results
        for start, end, _ in hits:
            assert text[start:end] != "[CITE:Transfer of Property Act, 1882, Section 54]"


# ---------------------------------------------------------------------------
# detect_operative_clauses
# ---------------------------------------------------------------------------

class TestDetectOperativeClauses:
    def test_now_this_deed_witnesseth(self):
        text = "NOW THIS DEED WITNESSETH as follows:"
        hits = detect_operative_clauses(text)
        assert len(hits) == 1
        assert hits[0][0] == 0

    def test_it_is_hereby_agreed(self):
        text = "IT IS HEREBY AGREED between the parties."
        hits = detect_operative_clauses(text)
        assert len(hits) == 1

    def test_the_parties_agree(self):
        text = "THE PARTIES AGREE to the following terms."
        hits = detect_operative_clauses(text)
        assert len(hits) == 1

    def test_in_consideration_whereof(self):
        text = "IN CONSIDERATION WHEREOF the parties execute this deed."
        hits = detect_operative_clauses(text)
        assert len(hits) == 1

    def test_case_insensitive(self):
        text = "Now this deed witnesseth as follows:"
        hits = detect_operative_clauses(text)
        assert len(hits) == 1

    def test_multiple_operative_clauses(self):
        text = (
            "NOW THIS DEED WITNESSETH:\n"
            "IT IS HEREBY AGREED that the parties shall comply."
        )
        hits = detect_operative_clauses(text)
        assert len(hits) == 2

    def test_no_operative_clauses(self):
        text = "This is a recital clause with no operative language."
        hits = detect_operative_clauses(text)
        assert hits == []

    def test_returns_start_end_tuples(self):
        text = "NOW THIS DEED WITNESSETH:"
        hits = detect_operative_clauses(text)
        assert len(hits) == 1
        start, end = hits[0]
        assert isinstance(start, int)
        assert isinstance(end, int)
        assert end > start


# ---------------------------------------------------------------------------
# parse_underline_markers
# ---------------------------------------------------------------------------

class TestParseUnderlineMarkers:
    def test_single_marker(self):
        text = "Hello <UNDERLINE>World</UNDERLINE> end."
        clean, ranges = parse_underline_markers(text)
        assert clean == "Hello World end."
        assert len(ranges) == 1
        start, end = ranges[0]
        assert clean[start:end] == "World"

    def test_multiple_markers(self):
        text = "<UNDERLINE>Vendor</UNDERLINE> and <UNDERLINE>Purchaser</UNDERLINE>."
        clean, ranges = parse_underline_markers(text)
        assert clean == "Vendor and Purchaser."
        assert len(ranges) == 2

    def test_no_markers(self):
        text = "No underline markers here."
        clean, ranges = parse_underline_markers(text)
        assert clean == text
        assert ranges == []

    def test_tags_stripped_from_clean_text(self):
        text = "<UNDERLINE>Party Name</UNDERLINE>"
        clean, _ = parse_underline_markers(text)
        assert "<UNDERLINE>" not in clean
        assert "</UNDERLINE>" not in clean

    def test_ranges_point_to_correct_text(self):
        text = "Before <UNDERLINE>Target Text</UNDERLINE> after."
        clean, ranges = parse_underline_markers(text)
        start, end = ranges[0]
        assert clean[start:end] == "Target Text"

    def test_multiline_content(self):
        text = "<UNDERLINE>Line one\nLine two</UNDERLINE>"
        clean, ranges = parse_underline_markers(text)
        assert clean == "Line one\nLine two"
        assert len(ranges) == 1


# ---------------------------------------------------------------------------
# get_underline_ranges
# ---------------------------------------------------------------------------

class TestGetUnderlineRanges:
    def test_marker_based_when_markers_present(self):
        text = "The <UNDERLINE>Vendor Shri Rajesh Kumar</UNDERLINE> agrees."
        ranges = get_underline_ranges(text, "sale_deed")
        assert len(ranges) > 0

    def test_heuristic_fallback_when_no_markers(self):
        text = "Vendor Shri Rajesh Kumar Sharma agrees to sell."
        ranges = get_underline_ranges(text, "sale_deed")
        assert len(ranges) > 0

    def test_no_ranges_for_plain_text(self):
        text = "This clause has no party names or operative phrases."
        ranges = get_underline_ranges(text, "sale_deed")
        assert ranges == []

    def test_ranges_are_sorted(self):
        text = "Vendor Shri A Kumar and Purchaser Smt B Patel."
        ranges = get_underline_ranges(text, "sale_deed")
        starts = [s for s, _ in ranges]
        assert starts == sorted(starts)

    def test_ranges_non_overlapping(self):
        text = "Vendor Shri A Kumar and Purchaser Smt B Patel."
        ranges = get_underline_ranges(text, "sale_deed")
        for i in range(len(ranges) - 1):
            assert ranges[i][1] <= ranges[i + 1][0]


# ---------------------------------------------------------------------------
# underline_parties_pdf_html
# ---------------------------------------------------------------------------

class TestUnderlinePartiesPdfHtml:
    def test_injects_u_tags(self):
        text = "Hello World"
        result = underline_parties_pdf_html(text, [(6, 11)])
        assert "<u>World</u>" in result

    def test_no_ranges_unchanged(self):
        text = "Hello World"
        result = underline_parties_pdf_html(text, [])
        assert result == text

    def test_multiple_ranges(self):
        text = "Vendor and Purchaser"
        result = underline_parties_pdf_html(text, [(0, 6), (11, 20)])
        assert "<u>Vendor</u>" in result
        assert "<u>Purchaser</u>" in result

    def test_surrounding_text_preserved(self):
        text = "Before Target After"
        result = underline_parties_pdf_html(text, [(7, 13)])
        assert result.startswith("Before ")
        assert result.endswith(" After")
        assert "<u>Target</u>" in result
