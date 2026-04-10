"""
Citation Formatter for the Maharashtra Legal Document Generation System.

This module implements the citation system used in Phase 1.5:

  1. LLM-generated text contains inline markers of the form:
         [CITE:Act Name, Year, Section X(Y)]
     optionally with a 4th part for court/legislature:
         [CITE:Act Name, Year, Section X(Y), Court Name]

  2. `collect_citations` parses those markers and returns a list of
     CitationMarker objects (markers are left intact in the returned text).

  3. `assign_reference_numbers` assigns sequential integers (1, 2, 3, …)
     in order of first appearance; duplicate citations reuse the same number.

  4. `replace_markers` substitutes every [CITE:…] with [N] (or with
     [UNGROUNDED — MANUAL REVIEW REQUIRED] for malformed markers).

  5. `build_citation_index` formats the end-of-document citation index
     per the Indian Legal Citation Standard:

         --- CITATION INDEX ---

         [1] [Transfer of Property Act, 1882] Section 54, Parliament of India, 1882
             Cited in clause(s): 3, 7

         [2] [Registration Act, 1908] Section 17(1)(b), Parliament of India, 1908
             Cited in clause(s): 8
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Compiled regex patterns
# ---------------------------------------------------------------------------

# Matches the full [CITE:...] token (non-greedy inner content)
_CITE_PATTERN = re.compile(r"\[CITE:([^\]]+)\]")

# Splits the inner content on commas, but only at the top level
# (commas inside parentheses are part of section references like "17(1)(b)")
_COMMA_SPLIT = re.compile(r",\s*")

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class CitationMarker:
    act_name: str
    year: str
    section: str
    court_or_legislature: str
    judgment_year: str | None
    clause_index: int            # order of appearance in text (0-based)
    first_appearance_index: int  # index of the first occurrence of this key


@dataclass
class CitationIndex:
    citations: list[CitationMarker]
    citation_map: dict[str, int]   # citation_key → reference_number
    formatted_index: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize_citation_key(act: str, year: str, section: str) -> str:
    """Return a normalised, case-insensitive key for deduplication.

    Format: ``{act.lower().strip()}|{year}|{section.lower().strip()}``
    """
    return f"{act.lower().strip()}|{year.strip()}|{section.lower().strip()}"


def collect_citations(text: str) -> tuple[str, list[CitationMarker]]:
    """Parse ``[CITE:…]`` markers from *text*.

    Returns ``(text, markers)`` where *text* is unchanged (markers intact)
    and *markers* is a list of :class:`CitationMarker` objects in order of
    appearance.  Malformed markers are skipped with a warning.
    """
    markers: list[CitationMarker] = []
    # Track which normalised keys we have already seen so we can set
    # first_appearance_index correctly.
    seen: dict[str, int] = {}   # key → clause_index of first appearance

    for clause_index, match in enumerate(_CITE_PATTERN.finditer(text)):
        raw = match.group(1)
        parts = [p.strip() for p in _COMMA_SPLIT.split(raw)]

        if len(parts) < 3:
            logger.warning(
                "Malformed citation marker (fewer than 3 parts): [CITE:%s]", raw
            )
            continue

        act_name = parts[0]
        year = parts[1]
        section = parts[2]
        court_or_legislature = parts[3] if len(parts) >= 4 else "Parliament of India"

        key = normalize_citation_key(act_name, year, section)
        if key not in seen:
            seen[key] = clause_index

        markers.append(
            CitationMarker(
                act_name=act_name,
                year=year,
                section=section,
                court_or_legislature=court_or_legislature,
                judgment_year=year,   # default to act year per Indian convention
                clause_index=clause_index,
                first_appearance_index=seen[key],
            )
        )

    return text, markers


def assign_reference_numbers(citations: list[CitationMarker]) -> dict[str, int]:
    """Assign sequential reference numbers in order of first appearance.

    Duplicate citations (same normalised key) reuse the same number.

    Returns a ``dict`` mapping *citation_key* → *reference_number*.
    """
    ref_map: dict[str, int] = {}
    counter = 1

    # Sort by first_appearance_index so numbers are assigned in document order
    for marker in sorted(citations, key=lambda m: m.first_appearance_index):
        key = normalize_citation_key(marker.act_name, marker.year, marker.section)
        if key not in ref_map:
            ref_map[key] = counter
            counter += 1

    return ref_map


def replace_markers(text: str, citation_map: dict[str, int]) -> str:
    """Replace every ``[CITE:…]`` marker with ``[N]``.

    Malformed markers (cannot be parsed into ≥3 parts) are replaced with
    ``[UNGROUNDED — MANUAL REVIEW REQUIRED]`` and a warning is logged.
    """

    def _replace(match: re.Match) -> str:
        raw = match.group(1)
        parts = [p.strip() for p in _COMMA_SPLIT.split(raw)]

        if len(parts) < 3:
            logger.warning(
                "Malformed citation marker during replacement (fewer than 3 parts): "
                "[CITE:%s] — substituting with UNGROUNDED marker",
                raw,
            )
            return "[UNGROUNDED \u2014 MANUAL REVIEW REQUIRED]"

        act_name, year, section = parts[0], parts[1], parts[2]
        key = normalize_citation_key(act_name, year, section)

        if key not in citation_map:
            logger.warning(
                "Citation key not found in citation_map: %s — substituting with "
                "UNGROUNDED marker",
                key,
            )
            return "[UNGROUNDED \u2014 MANUAL REVIEW REQUIRED]"

        return f"[{citation_map[key]}]"

    return _CITE_PATTERN.sub(_replace, text)


def build_citation_index(citations: list[CitationMarker]) -> str:
    """Format the end-of-document citation index.

    Returns a ready-to-append text block.  If *citations* is empty, returns
    the zero-citation placeholder.

    Format per Indian Legal Citation Standard::

        --- CITATION INDEX ---

        [1] [Act Name, Year] Section X(Y), Court/Legislature, Year
            Cited in clause(s): 1, 3, 7
    """
    heading = "--- CITATION INDEX ---"

    if not citations:
        return f"{heading}\n\n(No citations found in this document.)"

    # Build reference map and group clause numbers per citation key
    ref_map = assign_reference_numbers(citations)

    # clause_index is 0-based; display as 1-based
    clauses_by_key: dict[str, list[int]] = {}
    for marker in citations:
        key = normalize_citation_key(marker.act_name, marker.year, marker.section)
        clauses_by_key.setdefault(key, []).append(marker.clause_index + 1)

    # Build entries sorted by reference number
    entries_by_ref: dict[int, tuple[CitationMarker, list[int]]] = {}
    for marker in citations:
        key = normalize_citation_key(marker.act_name, marker.year, marker.section)
        ref = ref_map[key]
        if ref not in entries_by_ref:
            entries_by_ref[ref] = (marker, sorted(set(clauses_by_key[key])))

    lines = [heading, ""]
    for ref in sorted(entries_by_ref):
        marker, clause_nums = entries_by_ref[ref]
        entry_line = (
            f"[{ref}] [{marker.act_name}, {marker.year}] "
            f"{marker.section}, {marker.court_or_legislature}, {marker.year}"
        )
        clause_str = ", ".join(str(c) for c in clause_nums)
        lines.append(entry_line)
        lines.append(f"    Cited in clause(s): {clause_str}")
        lines.append("")

    # Remove trailing blank line
    if lines and lines[-1] == "":
        lines.pop()

    return "\n".join(lines)
