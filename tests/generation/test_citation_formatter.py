"""
tests/generation/test_citation_formatter.py

Unit tests for Citation_Formatter (src/generation/citation_formatter.py).

Covers:
  - collect_citations: parsing [CITE:...] markers
  - assign_reference_numbers: sequential numbering, deduplication
  - replace_markers: [CITE:...] → [N], malformed → UNGROUNDED
  - build_citation_index: heading, entries, zero-citation case
  - normalize_citation_key: case-insensitive deduplication
"""
from __future__ import annotations

import pytest

from src.generation.citation_formatter import (
    CitationMarker,
    assign_reference_numbers,
    build_citation_index,
    collect_citations,
    normalize_citation_key,
    replace_markers,
)


# ---------------------------------------------------------------------------
# normalize_citation_key
# ---------------------------------------------------------------------------

class TestNormalizeCitationKey:
    def test_lowercase_and_strip(self):
        key = normalize_citation_key("  Transfer of Property Act  ", "1882", "  Section 54  ")
        assert key == "transfer of property act|1882|section 54"

    def test_case_insensitive(self):
        k1 = normalize_citation_key("Registration Act", "1908", "Section 17(1)(b)")
        k2 = normalize_citation_key("REGISTRATION ACT", "1908", "SECTION 17(1)(B)")
        assert k1 == k2

    def test_pipe_delimiter(self):
        key = normalize_citation_key("Indian Contract Act", "1872", "Section 10")
        parts = key.split("|")
        assert len(parts) == 3


# ---------------------------------------------------------------------------
# collect_citations
# ---------------------------------------------------------------------------

class TestCollectCitations:
    def test_single_citation(self):
        text = "The property is transferred [CITE:Transfer of Property Act, 1882, Section 54]."
        returned_text, markers = collect_citations(text)
        assert returned_text == text  # text unchanged
        assert len(markers) == 1
        assert markers[0].act_name == "Transfer of Property Act"
        assert markers[0].year == "1882"
        assert markers[0].section == "Section 54"

    def test_multiple_citations(self):
        text = (
            "Clause 1 [CITE:Transfer of Property Act, 1882, Section 54]. "
            "Clause 2 [CITE:Registration Act, 1908, Section 17(1)(b)]."
        )
        _, markers = collect_citations(text)
        assert len(markers) == 2
        assert markers[0].act_name == "Transfer of Property Act"
        assert markers[1].act_name == "Registration Act"

    def test_duplicate_citation_both_returned(self):
        text = (
            "[CITE:Transfer of Property Act, 1882, Section 54] "
            "and again [CITE:Transfer of Property Act, 1882, Section 54]."
        )
        _, markers = collect_citations(text)
        assert len(markers) == 2  # both occurrences returned

    def test_custom_court(self):
        text = "[CITE:Maharashtra Stamp Act, 1958, Article 25, Maharashtra Legislature]"
        _, markers = collect_citations(text)
        assert markers[0].court_or_legislature == "Maharashtra Legislature"

    def test_default_court_parliament(self):
        text = "[CITE:Transfer of Property Act, 1882, Section 54]"
        _, markers = collect_citations(text)
        assert markers[0].court_or_legislature == "Parliament of India"

    def test_malformed_marker_skipped(self):
        text = "[CITE:OnlyOneField] valid [CITE:Act, 1900, Section 1]"
        _, markers = collect_citations(text)
        assert len(markers) == 1
        assert markers[0].act_name == "Act"

    def test_no_citations(self):
        text = "This document has no citation markers."
        returned_text, markers = collect_citations(text)
        assert returned_text == text
        assert markers == []

    def test_text_unchanged(self):
        text = "Before [CITE:Act, 2000, Section 1] after."
        returned_text, _ = collect_citations(text)
        assert returned_text == text


# ---------------------------------------------------------------------------
# assign_reference_numbers
# ---------------------------------------------------------------------------

class TestAssignReferenceNumbers:
    def test_sequential_numbering(self):
        text = (
            "[CITE:Transfer of Property Act, 1882, Section 54] "
            "[CITE:Registration Act, 1908, Section 17(1)(b)] "
            "[CITE:Maharashtra Stamp Act, 1958, Article 25]"
        )
        _, markers = collect_citations(text)
        ref_map = assign_reference_numbers(markers)
        assert len(ref_map) == 3
        assert sorted(ref_map.values()) == [1, 2, 3]

    def test_first_appearance_order(self):
        text = (
            "[CITE:Registration Act, 1908, Section 17] "
            "[CITE:Transfer of Property Act, 1882, Section 54]"
        )
        _, markers = collect_citations(text)
        ref_map = assign_reference_numbers(markers)
        reg_key = normalize_citation_key("Registration Act", "1908", "Section 17")
        tpa_key = normalize_citation_key("Transfer of Property Act", "1882", "Section 54")
        assert ref_map[reg_key] == 1
        assert ref_map[tpa_key] == 2

    def test_duplicate_reuses_same_number(self):
        text = (
            "[CITE:Transfer of Property Act, 1882, Section 54] "
            "[CITE:Registration Act, 1908, Section 17] "
            "[CITE:Transfer of Property Act, 1882, Section 54]"
        )
        _, markers = collect_citations(text)
        ref_map = assign_reference_numbers(markers)
        tpa_key = normalize_citation_key("Transfer of Property Act", "1882", "Section 54")
        assert ref_map[tpa_key] == 1
        assert len(ref_map) == 2  # only 2 unique citations

    def test_empty_citations(self):
        ref_map = assign_reference_numbers([])
        assert ref_map == {}

    def test_numbers_start_at_one(self):
        text = "[CITE:Act A, 2000, Section 1]"
        _, markers = collect_citations(text)
        ref_map = assign_reference_numbers(markers)
        assert min(ref_map.values()) == 1


# ---------------------------------------------------------------------------
# replace_markers
# ---------------------------------------------------------------------------

class TestReplaceMarkers:
    def test_single_replacement(self):
        text = "See [CITE:Transfer of Property Act, 1882, Section 54] for details."
        _, markers = collect_citations(text)
        ref_map = assign_reference_numbers(markers)
        result = replace_markers(text, ref_map)
        assert "[1]" in result
        assert "[CITE:" not in result

    def test_multiple_replacements(self):
        text = (
            "[CITE:Transfer of Property Act, 1882, Section 54] "
            "[CITE:Registration Act, 1908, Section 17]"
        )
        _, markers = collect_citations(text)
        ref_map = assign_reference_numbers(markers)
        result = replace_markers(text, ref_map)
        assert "[1]" in result
        assert "[2]" in result
        assert "[CITE:" not in result

    def test_duplicate_gets_same_number(self):
        text = (
            "[CITE:Transfer of Property Act, 1882, Section 54] "
            "and [CITE:Transfer of Property Act, 1882, Section 54]"
        )
        _, markers = collect_citations(text)
        ref_map = assign_reference_numbers(markers)
        result = replace_markers(text, ref_map)
        assert result.count("[1]") == 2

    def test_malformed_becomes_ungrounded(self):
        text = "[CITE:OnlyOneField] normal text"
        _, markers = collect_citations(text)
        ref_map = assign_reference_numbers(markers)
        result = replace_markers(text, ref_map)
        assert "UNGROUNDED" in result
        assert "[CITE:" not in result

    def test_surrounding_text_preserved(self):
        text = "Before [CITE:Act, 2000, Section 1] after."
        _, markers = collect_citations(text)
        ref_map = assign_reference_numbers(markers)
        result = replace_markers(text, ref_map)
        assert result.startswith("Before ")
        assert result.endswith(" after.")

    def test_no_markers_unchanged(self):
        text = "No citation markers here."
        result = replace_markers(text, {})
        assert result == text


# ---------------------------------------------------------------------------
# build_citation_index
# ---------------------------------------------------------------------------

class TestBuildCitationIndex:
    def test_heading_present(self):
        text = "[CITE:Transfer of Property Act, 1882, Section 54]"
        _, markers = collect_citations(text)
        index = build_citation_index(markers)
        assert "CITATION INDEX" in index

    def test_numbered_entries(self):
        text = (
            "[CITE:Transfer of Property Act, 1882, Section 54] "
            "[CITE:Registration Act, 1908, Section 17(1)(b)]"
        )
        _, markers = collect_citations(text)
        index = build_citation_index(markers)
        assert "[1]" in index
        assert "[2]" in index

    def test_entry_contains_act_name_and_year(self):
        text = "[CITE:Transfer of Property Act, 1882, Section 54]"
        _, markers = collect_citations(text)
        index = build_citation_index(markers)
        assert "Transfer of Property Act" in index
        assert "1882" in index

    def test_entry_contains_section(self):
        text = "[CITE:Registration Act, 1908, Section 17(1)(b)]"
        _, markers = collect_citations(text)
        index = build_citation_index(markers)
        assert "Section 17(1)(b)" in index

    def test_entry_contains_court(self):
        text = "[CITE:Maharashtra Stamp Act, 1958, Article 25, Maharashtra Legislature]"
        _, markers = collect_citations(text)
        index = build_citation_index(markers)
        assert "Maharashtra Legislature" in index

    def test_cited_in_clauses_present(self):
        text = "[CITE:Transfer of Property Act, 1882, Section 54]"
        _, markers = collect_citations(text)
        index = build_citation_index(markers)
        assert "Cited in clause(s):" in index

    def test_zero_citations_placeholder(self):
        index = build_citation_index([])
        assert "CITATION INDEX" in index
        assert "No citations found" in index

    def test_duplicate_citation_single_entry(self):
        text = (
            "[CITE:Transfer of Property Act, 1882, Section 54] "
            "[CITE:Transfer of Property Act, 1882, Section 54]"
        )
        _, markers = collect_citations(text)
        index = build_citation_index(markers)
        # Should appear only once as [1]
        assert index.count("[1]") == 1
        assert "[2]" not in index


# ---------------------------------------------------------------------------
# End-to-end pipeline test
# ---------------------------------------------------------------------------

class TestCitationPipeline:
    def test_full_pipeline(self):
        """Full pipeline: collect → assign → replace → build index."""
        text = (
            "The sale is governed by [CITE:Transfer of Property Act, 1882, Section 54]. "
            "Registration is required under [CITE:Registration Act, 1908, Section 17(1)(b)]. "
            "Stamp duty applies per [CITE:Maharashtra Stamp Act, 1958, Article 25, Maharashtra Legislature]. "
            "TPA applies again [CITE:Transfer of Property Act, 1882, Section 54]."
        )
        _, markers = collect_citations(text)
        ref_map = assign_reference_numbers(markers)
        replaced = replace_markers(text, ref_map)
        index = build_citation_index(markers)

        # 3 unique citations
        assert len(ref_map) == 3
        # Inline markers replaced
        assert "[CITE:" not in replaced
        assert "[1]" in replaced
        assert "[2]" in replaced
        assert "[3]" in replaced
        # TPA appears twice → both [1]
        assert replaced.count("[1]") == 2
        # Index has heading and 3 entries
        assert "CITATION INDEX" in index
        assert "[1]" in index
        assert "[2]" in index
        assert "[3]" in index
