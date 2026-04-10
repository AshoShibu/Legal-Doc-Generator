"""
Formatting Engine for Maharashtra Legal Document Generation System.

This module detects and applies underlining to party names and operative clause
headings in generated legal documents, conforming to Maharashtra legal drafting
conventions.

Two detection strategies are supported:
1. Marker-based: parse ``<UNDERLINE>...</UNDERLINE>`` tags emitted by the LLM.
2. Heuristic fallback: regex-based detection of party names and operative
   clause headings when the LLM omits formatting markers.

All regex patterns are compiled once at module load time for efficiency.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class FormattingRule:
    pattern: re.Pattern
    format_type: str  # "underline"
    scope: str        # "party_name" | "operative_clause"


@dataclass
class UnderlineRange:
    start: int        # character offset in text (inclusive)
    end: int          # character offset in text (exclusive)
    scope: str        # "party_name" | "operative_clause"
    matched_text: str


# ---------------------------------------------------------------------------
# Module-level pattern compilation
# ---------------------------------------------------------------------------

# Optional title prefix (Shri / Smt / Mr / Mrs / Dr / M/s)
_TITLE = r"(?:Shri|Smt|Mr\.?|Mrs\.?|Dr\.?|M/s\.?)[ \t]+"

# A capitalized name word (allows hyphens, e.g. "Kumar-Sharma")
_NAME_WORD = r"[A-Z][A-Za-z]+(?:-[A-Z][A-Za-z]+)?"

# Full name: optional title + one or more capitalized words
_FULL_NAME = rf"(?:{_TITLE})?{_NAME_WORD}(?:[ \t]+{_NAME_WORD})*"

# Roles grouped by document type
_ROLES_BY_DOC_TYPE: dict[str, list[str]] = {
    "sale_deed":          ["Vendor", "Purchaser", "Vendee"],
    "mortgage_deed":      ["Mortgagor", "Mortgagee"],
    "power_of_attorney":  ["Principal", "Attorney", "Agent"],
    "leave_and_license":  ["Licensor", "Licensee"],
    "gift_deed":          ["Donor", "Donee"],
    "conveyance_deed":    ["Conveyor", "Transferee"],
    "affidavit":          ["Deponent"],
}

# All roles across every document type (used as fallback)
_ALL_ROLES: list[str] = sorted(
    {role for roles in _ROLES_BY_DOC_TYPE.values() for role in roles},
    key=len,
    reverse=True,  # longer first to avoid partial matches
)


def _build_party_patterns(roles: list[str]) -> list[tuple[re.Pattern, str]]:
    """
    Build a list of (compiled_pattern, role) pairs for the given roles.

    Each pattern matches:
    - A full name occurrence: ``<role> [Title] Capitalized Name Sequence``
    - A standalone role reference: ``the <role>`` (bare keyword)
    """
    patterns: list[tuple[re.Pattern, str]] = []
    for role in roles:
        # Full name pattern: role keyword followed by optional title + name
        full_name_pat = re.compile(
            rf"\b{re.escape(role)}[ \t]+{_FULL_NAME}",
            re.UNICODE,
        )
        # Standalone reference: "the Vendor", "The Vendor", etc.
        standalone_pat = re.compile(
            rf"\bthe[ \t]+{re.escape(role)}\b",
            re.IGNORECASE | re.UNICODE,
        )
        patterns.append((full_name_pat, role))
        patterns.append((standalone_pat, role))
    return patterns


# Standalone titled-name pattern: Shri/Smt/Mr/Mrs/Dr followed by a full name
# Catches bare names like "Shri Vikram Anand Kulkarni" anywhere in the text
_TITLED_NAME_PATTERN = re.compile(
    rf"(?:Shri|Smt\.?|Mr\.?|Mrs\.?|Dr\.?|M/s\.?)[ \t]+{_NAME_WORD}(?:[ \t]+{_NAME_WORD})*",
    re.UNICODE,
)


# Pre-compile party patterns for every known doc_type
_PARTY_PATTERNS: dict[str, list[tuple[re.Pattern, str]]] = {
    doc_type: _build_party_patterns(roles)
    for doc_type, roles in _ROLES_BY_DOC_TYPE.items()
}

# Fallback patterns using all roles
_PARTY_PATTERNS_FALLBACK: list[tuple[re.Pattern, str]] = _build_party_patterns(_ALL_ROLES)

# Operative clause pattern — compiled once
_OPERATIVE_PATTERN = re.compile(
    r"(?:NOW[ \t]+THIS[ \t]+DEED[ \t]+WITNESSETH"
    r"|IT[ \t]+IS[ \t]+HEREBY[ \t]+AGREED"
    r"|THE[ \t]+PARTIES[ \t]+AGREE"
    r"|IN[ \t]+CONSIDERATION[ \t]+WHEREOF)"
    r"[ \t]*:?",
    re.IGNORECASE,
)

# Underline marker pattern — compiled once
_UNDERLINE_PATTERN = re.compile(r"<UNDERLINE>(.*?)</UNDERLINE>", re.DOTALL)

# Patterns for spans that must NOT be underlined
_CITE_MARKER_PATTERN = re.compile(r"\[CITE:[^\]]*\]")
_ALL_CAPS_LINE_PATTERN = re.compile(r"^[A-Z0-9 \t\-:,\.]+$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Exclusion helpers
# ---------------------------------------------------------------------------


def _build_excluded_ranges(text: str) -> list[tuple[int, int]]:
    """
    Return character ranges that must not be underlined:
    - ``[CITE:...]`` markers
    - ALL-CAPS lines (section headings / document header)
    """
    excluded: list[tuple[int, int]] = []
    for m in _CITE_MARKER_PATTERN.finditer(text):
        excluded.append((m.start(), m.end()))
    for m in _ALL_CAPS_LINE_PATTERN.finditer(text):
        # Only treat as a heading if the line is at least 4 chars
        if m.end() - m.start() >= 4:
            excluded.append((m.start(), m.end()))
    return excluded


def _is_excluded(start: int, end: int, excluded: list[tuple[int, int]]) -> bool:
    """Return True if [start, end) overlaps any excluded range."""
    for ex_start, ex_end in excluded:
        if start < ex_end and end > ex_start:
            return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_party_names(text: str, doc_type: str) -> list[tuple[int, int, str]]:
    """
    Detect party names in *text* using document-type-specific patterns.

    Returns a list of ``(start_offset, end_offset, party_role)`` tuples for
    every occurrence found.  Matches inside ``[CITE:...]`` markers or on
    ALL-CAPS lines (section headings / document header) are excluded.

    Parameters
    ----------
    text:
        The full document text to search.
    doc_type:
        One of the recognised document-type keys (e.g. ``"sale_deed"``).
        If unrecognised, patterns for all roles are used as a fallback.
    """
    patterns = _PARTY_PATTERNS.get(doc_type)
    if patterns is None:
        logger.warning(
            "detect_party_names: unrecognised doc_type %r — using all-role fallback",
            doc_type,
        )
        patterns = _PARTY_PATTERNS_FALLBACK

    excluded = _build_excluded_ranges(text)
    results: list[tuple[int, int, str]] = []
    seen: set[tuple[int, int]] = set()

    for pattern, role in patterns:
        for m in pattern.finditer(text):
            span = (m.start(), m.end())
            if span in seen:
                continue
            if _is_excluded(m.start(), m.end(), excluded):
                continue
            seen.add(span)
            results.append((m.start(), m.end(), role))

    # Also catch bare titled names: Shri/Smt/Mr/Mrs/Dr + capitalized name
    # e.g. "Shri Vikram Anand Kulkarni" appearing without a role keyword
    for m in _TITLED_NAME_PATTERN.finditer(text):
        span = (m.start(), m.end())
        if span in seen:
            continue
        if _is_excluded(m.start(), m.end(), excluded):
            continue
        seen.add(span)
        results.append((m.start(), m.end(), "named_party"))

    results.sort(key=lambda t: t[0])
    return results


def detect_operative_clauses(text: str) -> list[tuple[int, int]]:
    """
    Detect operative clause headings in *text*.

    Recognised phrases (case-insensitive, optional trailing colon):
    - ``NOW THIS DEED WITNESSETH``
    - ``IT IS HEREBY AGREED``
    - ``THE PARTIES AGREE``
    - ``IN CONSIDERATION WHEREOF``

    Returns a list of ``(start_offset, end_offset)`` tuples.
    """
    results: list[tuple[int, int]] = []
    for m in _OPERATIVE_PATTERN.finditer(text):
        results.append((m.start(), m.end()))
    return results


def parse_underline_markers(text: str) -> tuple[str, list[tuple[int, int]]]:
    """
    Parse ``<UNDERLINE>...</UNDERLINE>`` markers from LLM output.

    Returns ``(clean_text, ranges)`` where:
    - *clean_text* is the text with all ``<UNDERLINE>`` / ``</UNDERLINE>``
      tags stripped.
    - *ranges* is a list of ``(start, end)`` character offsets in
      *clean_text* pointing to the underlined spans.

    Nested markers are treated as flat — the inner content is underlined.
    """
    ranges: list[tuple[int, int]] = []
    clean_parts: list[str] = []
    cursor = 0          # position in original text
    offset_delta = 0    # cumulative chars removed by stripping tags

    for m in _UNDERLINE_PATTERN.finditer(text):
        # Text before this match
        clean_parts.append(text[cursor:m.start()])
        # The underlined content (group 1)
        inner = m.group(1)
        start_in_clean = m.start() - offset_delta
        end_in_clean = start_in_clean + len(inner)
        ranges.append((start_in_clean, end_in_clean))
        clean_parts.append(inner)
        # Tags removed: len("<UNDERLINE>") + len("</UNDERLINE>") = 11 + 12 = 23
        offset_delta += len(m.group(0)) - len(inner)
        cursor = m.end()

    # Remaining text after last match
    clean_parts.append(text[cursor:])
    clean_text = "".join(clean_parts)
    return clean_text, ranges


# ---------------------------------------------------------------------------
# DOCX underlining
# ---------------------------------------------------------------------------


def apply_underline_docx(doc, ranges: list[tuple[int, int]]):
    """
    Apply underlining to party-name spans in a python-docx ``Document``.

    The function walks every paragraph's runs, reconstructs a flat character
    offset map, and splits runs at the boundaries given in *ranges* so that
    the underlined spans become their own runs with ``font.underline = True``.

    Falls back to returning *doc* unmodified (plain export) if any error
    occurs, logging a warning.

    Parameters
    ----------
    doc:
        A ``python-docx`` ``Document`` instance (modified in-place).
    ranges:
        List of ``(start, end)`` character offsets **within the full
        concatenated paragraph text** to underline.  Offsets are
        paragraph-local — pass ranges from a single paragraph at a time,
        or use :func:`underline_parties_docx` which handles the split.

    Returns
    -------
    doc
        The same ``Document`` instance (modified in-place).
    """
    if not ranges:
        return doc

    try:
        from docx.oxml.ns import qn  # noqa: F401 — validates python-docx is available

        for paragraph in doc.paragraphs:
            _underline_paragraph_docx(paragraph, ranges)

    except Exception as exc:
        logger.warning(
            "apply_underline_docx: underlining failed, returning plain document. "
            "Error: %s",
            exc,
        )

    return doc


def _underline_paragraph_docx(paragraph, ranges: list[tuple[int, int]]) -> None:
    """
    Split runs in *paragraph* so that character spans in *ranges* are
    underlined.  *ranges* must be relative to the paragraph's own text.
    """
    # Reconstruct full paragraph text and per-run offsets
    full_text = paragraph.text
    if not full_text:
        return

    # Build a list of (run, run_start, run_end) for the current runs
    run_spans: list[tuple[object, int, int]] = []
    cursor = 0
    for run in paragraph.runs:
        run_text = run.text or ""
        run_spans.append((run, cursor, cursor + len(run_text)))
        cursor += len(run_text)

    # Collect only ranges that overlap this paragraph
    relevant = [(s, e) for s, e in ranges if s < cursor and e > 0]
    if not relevant:
        return

    # Build a set of split points: boundaries of underline ranges
    split_points: set[int] = set()
    for s, e in relevant:
        split_points.add(max(0, s))
        split_points.add(min(cursor, e))

    # For each run, split at the relevant points and re-apply underline
    for run, run_start, run_end in run_spans:
        run_text = run.text or ""
        if not run_text:
            continue

        # Points within this run (relative to run start)
        local_points = sorted(
            {p - run_start for p in split_points if run_start < p < run_end}
        )
        if not local_points:
            # No split needed — just check if the whole run should be underlined
            if any(s <= run_start and e >= run_end for s, e in relevant):
                run.font.underline = True
            continue

        # Split the run into sub-runs at local_points
        boundaries = [0] + local_points + [len(run_text)]
        segments = [run_text[boundaries[i]:boundaries[i + 1]] for i in range(len(boundaries) - 1)]

        # Replace the original run's text with the first segment
        run.text = segments[0]
        seg_start = run_start
        seg_end = run_start + len(segments[0])
        if any(s <= seg_start and e >= seg_end for s, e in relevant):
            run.font.underline = True

        # Insert new runs for the remaining segments after the original run
        parent_p = run._r.getparent()
        insert_idx = list(parent_p).index(run._r) + 1
        for seg in segments[1:]:
            seg_start = seg_end
            seg_end = seg_start + len(seg)

            from docx.oxml import OxmlElement
            from copy import deepcopy

            new_r = deepcopy(run._r)
            # Update text node
            from docx.oxml.ns import qn
            t_elems = new_r.findall(qn("w:t"))
            if t_elems:
                t_elems[0].text = seg
            else:
                t = OxmlElement("w:t")
                t.text = seg
                new_r.append(t)

            # Set or clear underline on the rPr
            rpr = new_r.find(qn("w:rPr"))
            if rpr is None:
                rpr = OxmlElement("w:rPr")
                new_r.insert(0, rpr)

            u_elem = rpr.find(qn("w:u"))
            should_underline = any(s <= seg_start and e >= seg_end for s, e in relevant)
            if should_underline:
                if u_elem is None:
                    u_elem = OxmlElement("w:u")
                    rpr.append(u_elem)
                u_elem.set(qn("w:val"), "single")
            else:
                if u_elem is not None:
                    rpr.remove(u_elem)

            parent_p.insert(insert_idx, new_r)
            insert_idx += 1


# ---------------------------------------------------------------------------
# PDF underlining
# ---------------------------------------------------------------------------


def apply_underline_pdf(pdf_content: bytes, ranges: list[tuple[int, int]]) -> bytes:
    """
    Apply underlining to party-name spans in a reportlab-generated PDF.

    Because reportlab renders to a byte stream and does not expose a
    post-processing API for character-level underlining, this function
    works at the **story level**: it expects to be called *before* PDF
    rendering by injecting ``<u>...</u>`` tags into the paragraph HTML
    that reportlab's ``Paragraph`` flowable understands.

    In practice, use :func:`underline_parties_pdf_html` to pre-process
    paragraph text before passing it to reportlab, rather than calling
    this function on already-rendered bytes.

    Falls back to returning *pdf_content* unmodified (plain export) if
    any error occurs, logging a warning.

    Parameters
    ----------
    pdf_content:
        Raw PDF bytes (returned as-is — see note above).
    ranges:
        List of ``(start, end)`` character offsets (unused in byte mode).

    Returns
    -------
    bytes
        *pdf_content* unchanged (underlining must be applied pre-render).
    """
    if not ranges:
        return pdf_content

    logger.warning(
        "apply_underline_pdf: post-render byte-level underlining is not supported "
        "by reportlab. Use underline_parties_pdf_html() to inject <u> tags before "
        "rendering. Returning plain PDF."
    )
    return pdf_content


def underline_parties_pdf_html(text: str, ranges: list[tuple[int, int]]) -> str:
    """
    Inject reportlab ``<u>...</u>`` tags into *text* at the given *ranges*.

    This is the correct way to apply underlining in reportlab: transform
    the paragraph text **before** passing it to ``Paragraph()``.

    Falls back to returning *text* unmodified if any error occurs.

    Parameters
    ----------
    text:
        Paragraph text (may already contain reportlab XML markup).
    ranges:
        List of ``(start, end)`` character offsets to underline.

    Returns
    -------
    str
        Text with ``<u>...</u>`` tags inserted at the specified ranges.
    """
    if not ranges:
        return text

    try:
        # Sort ranges and merge overlapping ones
        merged = _merge_ranges(sorted(ranges))

        parts: list[str] = []
        cursor = 0
        for start, end in merged:
            if start > cursor:
                parts.append(text[cursor:start])
            parts.append(f"<u>{text[start:end]}</u>")
            cursor = end
        parts.append(text[cursor:])
        return "".join(parts)

    except Exception as exc:
        logger.warning(
            "underline_parties_pdf_html: failed to inject underline tags. "
            "Returning plain text. Error: %s",
            exc,
        )
        return text


# ---------------------------------------------------------------------------
# Heuristic fallback + high-level helpers
# ---------------------------------------------------------------------------


def get_underline_ranges(
    text: str,
    doc_type: str,
    *,
    parties_only: bool = True,
) -> list[tuple[int, int]]:
    """
    Return character ranges to underline in *text*.

    Strategy (in order):
    1. If ``<UNDERLINE>`` markers are present, use :func:`parse_underline_markers`.
    2. Otherwise, fall back to :func:`detect_party_names` (heuristic).

    Parameters
    ----------
    text:
        Document text (may contain ``<UNDERLINE>`` markers).
    doc_type:
        Document type key (e.g. ``"sale_deed"``).
    parties_only:
        When ``True`` (default), only party-name ranges are returned.
        Operative clause ranges are excluded per user preference.

    Returns
    -------
    list[tuple[int, int]]
        Sorted, non-overlapping ``(start, end)`` ranges.
    """
    # Strategy 1: marker-based
    if "<UNDERLINE>" in text:
        _, ranges = parse_underline_markers(text)
        return _merge_ranges(sorted(ranges))

    # Strategy 2: heuristic fallback — party names + operative clauses
    party_hits = detect_party_names(text, doc_type)
    ranges = [(s, e) for s, e, _role in party_hits]
    if not parties_only:
        op_hits = detect_operative_clauses(text)
        ranges += op_hits
    return _merge_ranges(sorted(ranges))


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping or adjacent ``(start, end)`` ranges."""
    if not ranges:
        return []
    merged: list[tuple[int, int]] = [ranges[0]]
    for start, end in ranges[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged
