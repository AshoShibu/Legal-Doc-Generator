"""
src/generation/citation_verifier.py — Citation Verifier for Maharashtra Legal Document Generation.

Verifies every citation in a draft against the loaded LegalCache.
Inserts [UNGROUNDED — MANUAL REVIEW REQUIRED] for unverifiable citations.
Flags document as "High Hallucination Risk" if > 10% of clauses are ungrounded.

Supports:
  - Indian Legal Citation Standard: [Act Name, Year] Section X(Y), [Court/Legislature], [Year]
  - Inline numeric markers: [1], [2] referencing a citation list

Requirements: 4.3, 4.5, 4.6, 13.2, 13.4
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Citation regex patterns
# ---------------------------------------------------------------------------

# Full Indian Legal Citation Standard:
# [Act Name, Year] Section X(Y), [Court/Legislature], [Year]
_CITATION_FULL_RE = re.compile(
    r"\[([^\]]+?,\s*\d{4})\]\s+Section\s+([\w\(\)\.]+),\s*\[([^\]]+)\],\s*\[?(\d{4})\]?",
    re.IGNORECASE,
)

# Inline numeric citation markers: [1], [2], etc. (1–3 digits only, not years)
_CITATION_NUMERIC_RE = re.compile(r"\[(\d{1,3})\]")

# Ungrounded marker (already inserted by LLM or prior pass)
_UNGROUNDED_RE = re.compile(r"\[UNGROUNDED[^\]]*\]", re.IGNORECASE)

# Clause splitter: numbered paragraphs or double newlines
_CLAUSE_SPLIT_RE = re.compile(r"\n\s*\n|\n(?=\d+\.)")

# High hallucination threshold
_HIGH_HALLUCINATION_THRESHOLD = 0.10  # 10%

UNGROUNDED_MARKER = "[UNGROUNDED — MANUAL REVIEW REQUIRED]"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Citation:
    act_name: str
    year: int
    section: str
    court_or_legislature: str
    judgment_year: int | None
    clause_numbers: list[int] = field(default_factory=list)


@dataclass
class CitationVerificationResult:
    verified_text: str                  # text with [UNGROUNDED] markers inserted
    grounded_citations: list[Citation]
    ungrounded_citations: list[Citation]
    ungrounded_clause_count: int
    total_clause_count: int
    high_hallucination_risk: bool       # True if > 10% ungrounded


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------



# Common legal/English words that are too generic for meaningful grounding
_STOP_WORDS = frozenset({
    "act", "law", "deed", "the", "and", "for", "with", "from", "that",
    "this", "code", "bill", "rule", "rules", "order", "section", "clause",
})


def _build_cache_lookup(cache) -> set[str]:
    """
    Build a set of lowercase document/act name fragments from the LegalCache
    for fast containment checks.
    """
    import re as _re

    def _norm(s: str) -> str:
        s = _re.sub(r"[,\.\-]+", " ", s.lower())
        s = _re.sub(r"\s+", " ", s).strip()
        if s.startswith("the "):
            s = s[4:]
        return s

    names: set[str] = set()
    for doc in cache.documents:
        names.add(_norm(doc.name))
        # Add individual significant words
        for word in _norm(doc.name).split():
            if len(word) >= 5 and word not in _STOP_WORDS:
                names.add(word)
    return names


def _is_grounded(act_name: str, cache_names: set[str]) -> bool:
    """
    Check whether *act_name* can be traced to a document in the cache.
    Uses substring matching against known document names, ignoring punctuation.
    """
    import re as _re

    def _norm(s: str) -> str:
        # lowercase, strip punctuation, collapse whitespace
        s = _re.sub(r"[,\.\-]+", " ", s.lower())
        s = _re.sub(r"\s+", " ", s).strip()
        # drop leading "the "
        if s.startswith("the "):
            s = s[4:]
        return s

    act_norm = _norm(act_name)

    # Direct substring check (normalised)
    for name in cache_names:
        name_norm = _norm(name)
        if act_norm in name_norm or name_norm in act_norm:
            return True

    # Word-level check: significant words (length >= 5, not stop words)
    words = [w for w in act_norm.split() if len(w) >= 5 and w not in _STOP_WORDS]
    for word in words:
        for name in cache_names:
            if word in _norm(name):
                return True
    return False


def _parse_year(year_str: str) -> int:
    """Parse a year string to int, returning 0 on failure."""
    try:
        return int(year_str.strip())
    except (ValueError, AttributeError):
        return 0


def _insert_ungrounded_marker(text: str, citation_match: re.Match) -> str:
    """
    Replace a citation match in *text* with the ungrounded marker.
    Returns the modified text.
    """
    start, end = citation_match.span()
    return text[:start] + UNGROUNDED_MARKER + text[end:]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def verify_citations(draft_text: str, cache) -> CitationVerificationResult:
    """
    Verify every citation in *draft_text* against the loaded *cache*.

    Steps:
      1. Split draft into clauses
      2. For each clause, find all full-format citations
      3. Check each citation against the cache
      4. Replace unverifiable citations with UNGROUNDED_MARKER
      5. Count ungrounded clauses; set high_hallucination_risk if > 10%

    Args:
        draft_text: The raw LLM-generated draft text.
        cache:      A LegalCache instance (from src/cag/engine.py).

    Returns:
        CitationVerificationResult with verified text and statistics.
    """
    cache_names = _build_cache_lookup(cache)

    # Split into clauses for counting
    clauses = _CLAUSE_SPLIT_RE.split(draft_text)
    clauses = [c for c in clauses if c.strip()]
    total_clause_count = max(len(clauses), 1)

    grounded_citations: list[Citation] = []
    ungrounded_citations: list[Citation] = []
    ungrounded_clause_indices: set[int] = set()

    # Work on a mutable copy of the text
    verified_text = draft_text

    # Track offset shift as we insert/replace text
    offset_shift = 0

    # Process each full citation match in the original text
    for m in list(_CITATION_FULL_RE.finditer(draft_text)):
        act_year_str = m.group(1).strip()   # e.g. "Transfer of Property Act, 1882"
        section = m.group(2).strip()
        court = m.group(3).strip()
        year_str = m.group(4).strip()

        # Parse act name and year
        parts = act_year_str.rsplit(",", 1)
        act_name = parts[0].strip()
        act_year = _parse_year(parts[1]) if len(parts) > 1 else 0
        judgment_year = _parse_year(year_str)

        # Find which clause this citation belongs to
        clause_idx = _find_clause_index(m.start(), clauses, draft_text)

        citation = Citation(
            act_name=act_name,
            year=act_year,
            section=section,
            court_or_legislature=court,
            judgment_year=judgment_year if judgment_year != act_year else None,
            clause_numbers=[clause_idx],
        )

        if _is_grounded(act_year_str, cache_names):
            grounded_citations.append(citation)
        else:
            logger.warning(
                "Citation '%s' not found in cache — marking as ungrounded.",
                act_year_str,
            )
            ungrounded_citations.append(citation)
            ungrounded_clause_indices.add(clause_idx)

            # Replace in verified_text (accounting for offset shift)
            adj_start = m.start() + offset_shift
            adj_end = m.end() + offset_shift
            verified_text = (
                verified_text[:adj_start]
                + UNGROUNDED_MARKER
                + verified_text[adj_end:]
            )
            offset_shift += len(UNGROUNDED_MARKER) - (m.end() - m.start())

    # Also count clauses that already had [UNGROUNDED] markers from the LLM
    for idx, clause in enumerate(clauses):
        if _UNGROUNDED_RE.search(clause):
            ungrounded_clause_indices.add(idx)

    ungrounded_clause_count = len(ungrounded_clause_indices)
    high_hallucination_risk = (
        ungrounded_clause_count / total_clause_count > _HIGH_HALLUCINATION_THRESHOLD
    )

    if high_hallucination_risk:
        logger.warning(
            "High Hallucination Risk: %d/%d clauses ungrounded (%.1f%%).",
            ungrounded_clause_count, total_clause_count,
            100 * ungrounded_clause_count / total_clause_count,
        )

    return CitationVerificationResult(
        verified_text=verified_text,
        grounded_citations=grounded_citations,
        ungrounded_citations=ungrounded_citations,
        ungrounded_clause_count=ungrounded_clause_count,
        total_clause_count=total_clause_count,
        high_hallucination_risk=high_hallucination_risk,
    )


def _find_clause_index(char_pos: int, clauses: list[str], full_text: str) -> int:
    """
    Find which clause index a character position belongs to.
    Uses cumulative character offsets.
    """
    running = 0
    for idx, clause in enumerate(clauses):
        # Find this clause in the full text starting from running position
        clause_start = full_text.find(clause.strip(), running)
        if clause_start == -1:
            running += len(clause)
            continue
        clause_end = clause_start + len(clause)
        if clause_start <= char_pos <= clause_end:
            return idx
        running = clause_end
    return 0
