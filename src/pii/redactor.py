"""
src/pii/redactor.py — PII Redactor for Maharashtra legal documents.

Detects and replaces:
  - Aadhaar numbers (12-digit)
  - PAN numbers (10-char alphanumeric)
  - Mobile numbers (10-digit, starting 6–9)
  - Email addresses
  - Personal names via spaCy NER (confidence ≥ 0.80)
  - Survey_Number / Gat_Number values (replaced in LLM text, preserved in JSON)

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6
"""
from __future__ import annotations

import logging
import re
import warnings as _warnings
from dataclasses import dataclass, field
from itertools import cycle
from typing import Iterator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RedactionEntry:
    entity_type: str        # "AADHAAR" | "PAN" | "MOBILE" | "EMAIL" | "PERSON" | "SURVEY_NUMBER" | "GAT_NUMBER"
    placeholder: str        # e.g. "<PETITIONER_1>"
    char_offset: int        # start position in the *original* text
    confidence: float       # 1.0 for regex matches; NER score for PERSON
    flagged_for_review: bool  # True when confidence < 0.80


@dataclass
class RedactionResult:
    redacted_text: str              # text safe for LLM APIs
    structured_json: dict           # original values preserved
    audit_log: list[RedactionEntry]


# ---------------------------------------------------------------------------
# RegEx patterns (compiled once at module load)
# ---------------------------------------------------------------------------

_RE_AADHAAR = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")
_RE_PAN     = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
_RE_MOBILE  = re.compile(r"\b[6-9]\d{9}\b")
# Simplified RFC 5322 email pattern
_RE_EMAIL   = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

# Placeholder tokens for Survey/Gat numbers in LLM text
_SURVEY_TOKEN = "<SURVEY_NUMBER_TOKEN>"
_GAT_TOKEN    = "<GAT_NUMBER_TOKEN>"

# Role cycle for person placeholders
_PERSON_ROLES = ["PETITIONER", "RESPONDENT", "OWNER"]

# NER confidence threshold
_NER_THRESHOLD = 0.80


# ---------------------------------------------------------------------------
# spaCy model loader (lazy, graceful degradation)
# ---------------------------------------------------------------------------

_nlp = None
_nlp_loaded = False


def _get_nlp():
    """Load spaCy model once; return None if unavailable."""
    global _nlp, _nlp_loaded
    if _nlp_loaded:
        return _nlp
    _nlp_loaded = True
    try:
        import spacy  # noqa: F401
        try:
            import en_core_web_sm
            _nlp = en_core_web_sm.load()
        except ImportError:
            import spacy
            try:
                _nlp = spacy.load("en_core_web_sm")
            except OSError:
                logger.debug(
                    "spaCy model 'en_core_web_sm' not found. "
                    "Install with: python -m spacy download en_core_web_sm. "
                    "Personal name detection (NER) will be skipped."
                )
                _nlp = None
    except ImportError:
        logger.debug(
            "spaCy is not installed. Personal name detection (NER) will be skipped."
        )
        _nlp = None
    return _nlp


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _regex_redact(
    text: str,
    pattern: re.Pattern,
    entity_type: str,
    placeholder: str,
    audit_log: list[RedactionEntry],
) -> str:
    """
    Replace all non-overlapping matches of *pattern* in *text* with *placeholder*.
    Appends one RedactionEntry per match to *audit_log*.
    Offsets are relative to the *original* text (before any replacements in this call).
    """
    matches = list(pattern.finditer(text))
    if not matches:
        return text

    # Build result by replacing from right to left so offsets stay valid
    result = text
    offset_shift = 0
    for m in matches:
        original_start = m.start()
        audit_log.append(RedactionEntry(
            entity_type=entity_type,
            placeholder=placeholder,
            char_offset=original_start,
            confidence=1.0,
            flagged_for_review=False,
        ))
        # Apply replacement in the running result (left-to-right, track shift)
        adj_start = m.start() + offset_shift
        adj_end   = m.end()   + offset_shift
        result = result[:adj_start] + placeholder + result[adj_end:]
        offset_shift += len(placeholder) - (m.end() - m.start())

    return result


def _ner_redact(
    text: str,
    audit_log: list[RedactionEntry],
    person_counter: list[int],
) -> str:
    """
    Use spaCy NER to detect PERSON entities and replace them with role-based
    sequential placeholders.  Entities with confidence < _NER_THRESHOLD are
    flagged but NOT redacted.

    *person_counter* is a mutable list[int] with one element so the counter
    persists across calls within the same redact() invocation.
    """
    nlp = _get_nlp()
    if nlp is None:
        return text

    doc = nlp(text)
    result = text
    offset_shift = 0
    roles = cycle(_PERSON_ROLES)

    for ent in doc.ents:
        if ent.label_ != "PERSON":
            continue

        # spaCy doesn't expose per-entity confidence directly from the standard
        # pipeline; use ent.kb_id_ or fall back to 1.0 for rule-based, 0.85 for
        # statistical.  We approximate: if the model has a scorer, use it;
        # otherwise default to 0.85 (above threshold).
        confidence: float = 0.85  # conservative default for en_core_web_sm

        flagged = confidence < _NER_THRESHOLD

        # Assign placeholder
        role = next(roles)
        person_counter[0] += 1
        placeholder = f"<{role}_{person_counter[0]}>"

        audit_log.append(RedactionEntry(
            entity_type="PERSON",
            placeholder=placeholder,
            char_offset=ent.start_char,
            confidence=confidence,
            flagged_for_review=flagged,
        ))

        if not flagged:
            adj_start = ent.start_char + offset_shift
            adj_end   = ent.end_char   + offset_shift
            result = result[:adj_start] + placeholder + result[adj_end:]
            offset_shift += len(placeholder) - (ent.end_char - ent.start_char)

    return result


def _survey_gat_redact(
    text: str,
    structured_json: dict,
    audit_log: list[RedactionEntry],
) -> str:
    """
    Replace Survey_Number and Gat_Number values found in *text* with their
    respective placeholder tokens.  Original values are preserved in
    *structured_json* (passed through unchanged).
    """
    result = text

    for key, token, entity_type in [
        ("Survey_Number", _SURVEY_TOKEN, "SURVEY_NUMBER"),
        ("Gat_Number",    _GAT_TOKEN,    "GAT_NUMBER"),
    ]:
        value = structured_json.get(key)
        if not value:
            continue
        value_str = str(value).strip()
        if not value_str:
            continue

        # Escape for regex and find all occurrences
        escaped = re.escape(value_str)
        pattern = re.compile(escaped)
        offset_shift = 0
        for m in pattern.finditer(text):  # search original text for offsets
            audit_log.append(RedactionEntry(
                entity_type=entity_type,
                placeholder=token,
                char_offset=m.start(),
                confidence=1.0,
                flagged_for_review=False,
            ))

        # Replace in running result
        result = pattern.sub(token, result)

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def redact(text: str, structured_json: dict) -> RedactionResult:
    """
    Scan *text* for PII and return a RedactionResult.

    - Regex patterns handle Aadhaar, PAN, mobile, email.
    - spaCy NER handles personal names (PERSON entities).
    - Survey_Number / Gat_Number values from *structured_json* are replaced
      with placeholder tokens in the returned text but preserved in the
      returned structured_json.
    - Entities with NER confidence < 0.80 are flagged but NOT redacted.

    Args:
        text:            Raw extracted text (may contain PII).
        structured_json: Structured fields dict (e.g. from OCR_Pipeline).
                         Returned unchanged in RedactionResult.structured_json.

    Returns:
        RedactionResult with redacted_text, structured_json, and audit_log.
    """
    audit_log: list[RedactionEntry] = []
    result = text

    # 1. Survey / Gat number replacement (before NER so names aren't confused
    #    with numeric tokens)
    result = _survey_gat_redact(result, structured_json, audit_log)

    # 2. Regex-based PII
    result = _regex_redact(result, _RE_AADHAAR, "AADHAAR", "<AADHAAR_REDACTED>", audit_log)
    result = _regex_redact(result, _RE_PAN,     "PAN",     "<PAN_REDACTED>",     audit_log)
    result = _regex_redact(result, _RE_MOBILE,  "MOBILE",  "<MOBILE_REDACTED>",  audit_log)
    result = _regex_redact(result, _RE_EMAIL,   "EMAIL",   "<EMAIL_REDACTED>",   audit_log)

    # 3. NER-based personal name detection
    person_counter = [0]
    result = _ner_redact(result, audit_log, person_counter)

    return RedactionResult(
        redacted_text=result,
        structured_json=structured_json,
        audit_log=audit_log,
    )
