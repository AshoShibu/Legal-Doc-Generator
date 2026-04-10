"""
src/cag/engine.py — CAG Engine for Maharashtra Legal Document Generation.

Implements:
  - load_cache(document_type, llm_backend) -> LegalCache
  - generate_draft(fact_pattern, cache, doc_type) -> DraftResult
  - Session management: cache reuse within a session
  - Session log: records cache composition on every load
  - Ollama HTTP API integration for llama3:8b, qwen2:7b, mixtral:8x7b

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 4.1, 4.4, 4.8
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.cag.cache_loader import (
    CONTEXT_WINDOWS,
    CacheEntry,
    ManifestLoadResult,
    load_manifest,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM backend → Ollama model tag mapping
# ---------------------------------------------------------------------------

BACKEND_MODEL_TAGS: dict[str, str] = {
    "llama3_8b": "llama3:8b",
    "qwen2_7b": "qwen2:7b",
    "qwen2.5_7b": "qwen2.5:7b",
    "qwen2.5_1.5b": "qwen2.5:1.5b",
    "tinyllama_1.1b": "tinyllama:1.1b",
    "mixtral_8x7b": "mixtral:8x7b",
    "gpt_oss_120b": "gpt-oss:120b-cloud",
    # Groq cloud backends (fast, free tier)
    "groq_llama3_8b": "llama-3.1-8b-instant",
    "groq_llama3_70b": "llama-3.3-70b-versatile",
    "groq_mixtral": "mixtral-8x7b-32768",
}

# Groq backends — routed to Groq API instead of Ollama
_GROQ_BACKENDS = {"groq_llama3_8b", "groq_llama3_70b", "groq_mixtral"}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Citation:
    act_name: str
    section: str
    year: str
    clause_index: int           # which clause in the draft this grounds
    source_document: str        # name from CacheEntry


@dataclass
class LegalCache:
    documents: list[CacheEntry]
    total_tokens: int
    llm_backend: str            # "llama3_8b" | "qwen2_7b" | "mixtral_8x7b"
    session_id: str
    omitted_documents: list[str]  # truncated due to token limit
    document_type: str = ""     # e.g. "sale_deed"


@dataclass
class DraftResult:
    content: str
    citations: list[Citation]
    ungrounded_clauses: list[int]   # clause indices with no grounding
    session_id: str


# ---------------------------------------------------------------------------
# Session registry — maps session_id → LegalCache
# ---------------------------------------------------------------------------

_session_cache: dict[str, LegalCache] = {}

# ---------------------------------------------------------------------------
# Session log helpers
# ---------------------------------------------------------------------------


def _get_session_log_path() -> Path:
    output_dir = Path(os.environ.get("OUTPUT_DIR", "./output"))
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / "session_log.jsonl"


def _write_session_log(cache: LegalCache) -> None:
    """
    Append a session log entry for a cache load event.
    Records: session_id, timestamp, llm_backend, document_type,
             document names, token counts per document, total_tokens,
             omitted_documents.
    """
    entry = {
        "event": "cache_load",
        "session_id": cache.session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "llm_backend": cache.llm_backend,
        "document_type": cache.document_type,
        "total_tokens": cache.total_tokens,
        "omitted_documents": cache.omitted_documents,
        "documents": [
            {"name": doc.name, "tokens": doc.tokens, "priority": doc.priority}
            for doc in cache.documents
        ],
    }
    log_path = _get_session_log_path()
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.info(
            "Session log written for session_id=%s, document_type=%s, "
            "backend=%s, total_tokens=%d",
            cache.session_id, cache.document_type,
            cache.llm_backend, cache.total_tokens,
        )
    except Exception as exc:
        logger.warning("Failed to write session log: %s", exc)


# ---------------------------------------------------------------------------
# Ollama HTTP API helpers
# ---------------------------------------------------------------------------


def _get_ollama_url() -> str:
    return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")


def _call_groq(model_tag: str, prompt: str, timeout: int = 60) -> str:
    """
    Call the Groq cloud API (OpenAI-compatible) for fast inference.
    Reads GROQ_API_KEY from environment.

    Rate-limit handling: on HTTP 429, reads the Retry-After header (or falls
    back to exponential backoff: 2s → 4s → 8s) and retries up to 3 times
    before returning the unavailable marker.
    """
    try:
        import requests
    except ImportError as exc:
        raise ImportError("requests is required: pip install requests") from exc

    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        logger.error("GROQ_API_KEY not set. Cannot call Groq API.")
        return (
            "[GROQ_UNAVAILABLE — MANUAL REVIEW REQUIRED]\n"
            "Set GROQ_API_KEY environment variable to use Groq inference."
        )

    max_retries = 3
    backoff = 2  # seconds; doubles each retry

    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_tag,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 800,  # slots need 100-300 tokens; 800 is generous headroom
                },
                timeout=(10, timeout),
            )

            if resp.status_code == 429:
                if attempt >= max_retries:
                    logger.error(
                        "Groq 429 rate limit: all %d retries exhausted for model=%s.",
                        max_retries, model_tag,
                    )
                    return (
                        "[GROQ_UNAVAILABLE — MANUAL REVIEW REQUIRED]\n"
                        "Groq rate limit exceeded. Try again in a minute or switch to a local Ollama model."
                    )
                # Honour Retry-After if present, otherwise use exponential backoff
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else backoff * (2 ** attempt)
                logger.warning(
                    "Groq 429 rate limit (attempt %d/%d). Waiting %.1fs before retry.",
                    attempt + 1, max_retries, wait,
                )
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

        except Exception as exc:
            # Don't retry on non-429 errors
            logger.error("Groq API call failed for model=%s: %s", model_tag, exc)
            return (
                "[GROQ_UNAVAILABLE — MANUAL REVIEW REQUIRED]\n"
                f"Groq API call failed: {exc}"
            )

    # Should not reach here, but guard anyway
    return "[GROQ_UNAVAILABLE — MANUAL REVIEW REQUIRED]\nUnexpected retry loop exit."


def _call_ollama(model_tag: str, prompt: str, timeout: int = 900) -> str:
    """
    Call the Ollama /api/generate endpoint using streaming so the connection
    stays alive during slow CPU inference (no read timeout mid-generation).
    Falls back to a stub response if Ollama is unreachable.
    """
    try:
        import requests
    except ImportError as exc:
        raise ImportError("requests is required: pip install requests") from exc

    base_url = _get_ollama_url()

    # Use streaming=True so the socket stays open; collect chunks as they arrive.
    # connect timeout = 10s, read timeout = timeout (per-chunk, not total).
    try:
        chunks: list[str] = []
        with requests.post(
            f"{base_url}/api/generate",
            json={"model": model_tag, "prompt": prompt, "stream": True},
            timeout=(10, timeout),
            stream=True,
        ) as resp:
            resp.raise_for_status()
            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                try:
                    data = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                chunks.append(data.get("response", ""))
                if data.get("done"):
                    break
        result = "".join(chunks)
        if result:
            return result
    except Exception as exc:
        logger.warning("Ollama streaming /api/generate failed: %s", exc)

    # Fallback: non-streaming /api/chat with full timeout
    try:
        resp = requests.post(
            f"{base_url}/api/chat",
            json={
                "model": model_tag,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
            timeout=(10, timeout),
        )
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "")
    except Exception as exc:
        logger.error(
            "Ollama call failed for model=%s: %s. Returning stub response.",
            model_tag, exc,
        )
        return (
            "[OLLAMA_UNAVAILABLE — MANUAL REVIEW REQUIRED]\n"
            "The LLM backend could not be reached. "
            "Please ensure Ollama is running at the configured URL."
        )


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def _build_cache_context(cache: LegalCache, max_chars: int = 24000) -> str:
    """
    Assemble the legal cache documents into a single context block.

    Truncation is section-boundary-aware: rather than cutting at a raw
    character offset (which can slice mid-sentence), we find the last
    complete legal section boundary (e.g. "Section X." or a blank line
    followed by a capital letter) before the character limit and cut there.

    Each document's budget is min(tokens * 4 chars, remaining max_chars).
    """
    # Section boundary pattern: blank line + capital word, or "Section N" header
    _SECTION_BOUNDARY_RE = re.compile(
        r"(?:\n\n(?=[A-Z])|\n(?=Section\s+\d)|\n(?=\d+\.\s+[A-Z]))",
    )

    parts: list[str] = []
    total_chars = 0

    for doc in cache.documents:
        if total_chars >= max_chars:
            break
        header = f"=== {doc.name} ==="
        content = doc.content.strip() if doc.content else "[Content not available]"

        # Per-entry character budget
        entry_max = min(doc.tokens * 4, max_chars - total_chars)

        if len(content) > entry_max:
            # Find the last section boundary before entry_max
            truncated = content[:entry_max]
            boundaries = [m.start() for m in _SECTION_BOUNDARY_RE.finditer(truncated)]
            if boundaries:
                # Cut at the last clean boundary
                cut = boundaries[-1]
                content = content[:cut].rstrip() + "\n[... truncated at section boundary ...]"
            else:
                # No boundary found — fall back to sentence end
                last_period = truncated.rfind(". ")
                if last_period > entry_max // 2:
                    content = content[:last_period + 1] + "\n[... truncated ...]"
                else:
                    content = truncated + "\n[... truncated ...]"

        parts.append(f"{header}\n{content}")
        total_chars += len(header) + len(content)

    return "\n\n".join(parts)


def _build_citation_examples(cache_entries: list) -> str:
    """
    Build a concrete citation example string from the loaded cache entries.

    Maps each entry name to its correct legislature/court and enactment year
    so the LLM receives a filled-in example rather than placeholder tokens.
    """
    # Statutes enacted by Parliament of India
    _CENTRAL_ACTS = {
        "transfer of property act",
        "registration act",
        "indian evidence act",
        "indian contract act",
        "indian stamp act",
        "code of civil procedure",
        "limitation act",
        "specific relief act",
        "real estate regulation and development act",
        "rera",
    }
    # Statutes enacted by Maharashtra Legislature
    _MAHARASHTRA_ACTS = {
        "maharashtra rent control act",
        "maharashtra land revenue code",
        "mlrc",
        "maharashtra co-operative societies act",
        "maharashtra ownership flats act",
        "maharashtra regional and town planning act",
    }

    examples: list[str] = []
    for entry in cache_entries:
        name: str = entry.name  # e.g. "Registration Act, 1908"
        name_lower = name.lower()

        # Extract year from the entry name (last 4-digit number)
        year_match = re.search(r"\b(1[89]\d{2}|20\d{2})\b", name)
        year = year_match.group(1) if year_match else "Year"

        # Determine legislature/court
        if any(k in name_lower for k in _CENTRAL_ACTS):
            court = "Parliament of India"
        elif any(k in name_lower for k in _MAHARASHTRA_ACTS):
            court = "Maharashtra Legislature"
        elif "bhc" in name_lower or "judgment" in name_lower or "high court" in name_lower:
            court = "Bombay High Court"
        else:
            court = "Parliament of India"  # safe default for unrecognised acts

        # Only emit examples for Acts (not BHC judgments) to keep the example concise
        if "judgment" not in name_lower and "bhc" not in name_lower:
            # Guard: expand abbreviated court names to full form
            if court.lower() == "parliament":
                court = "Parliament of India"
            examples.append(f"[{name}] Section X(Y), {court}, {year}")

    if not examples:
        return "[Act Name, Year] Section X(Y), Parliament of India, Year"
    return examples[0]  # one concrete example is enough


def _build_slot_prompt(
    slot_name: str,
    fact_pattern: dict | str,
    cache_context: str,
    doc_type: str,
    cache_entries: list | None = None,
) -> str:
    """
    Build a focused single-slot prompt for Pass 1 of two-pass generation.

    Each call asks the LLM to write content for exactly one document section,
    grounded in the cache. Narrower task = fewer hallucinations.

    Slot descriptions and OPERATIVE_CLAUSE instructions are document-type-specific
    to ensure the LLM injects the correct intake fields and avoids generic boilerplate.
    """
    doc_type_display = doc_type.replace("_", " ").title()

    # -----------------------------------------------------------------------
    # Document-type-specific PARTIES_CLAUSE descriptions
    # -----------------------------------------------------------------------
    _PARTIES_BY_DOC: dict[str, str] = {
        "sale_deed": (
            "the opening clause identifying the Vendor (Seller) and Purchaser (Buyer) — "
            "use the exact seller_name, seller_address, buyer_name, buyer_address from the FACTS; "
            "state their roles as 'Vendor' and 'Purchaser' respectively"
        ),
        "mortgage_deed": (
            "the opening clause identifying the Mortgagor (Borrower) and Mortgagee (Lender) — "
            "use the exact mortgagor_name, mortgagor_address, mortgagee_name, mortgagee_address from the FACTS; "
            "state their roles as 'Mortgagor' and 'Mortgagee' respectively"
        ),
        "power_of_attorney": (
            "the opening clause identifying the Principal (Grantor) and Attorney (Agent) — "
            "FIRST LINE must state explicitly whether this is a 'General Power of Attorney' "
            "(broad authority over all acts) or a 'Special Power of Attorney' (limited to the "
            "specific transaction described in the FACTS); derive the type from the "
            "poa_type or scope_of_authority field in the FACTS; "
            "use the exact principal_name, principal_address, attorney_name, attorney_address from the FACTS; "
            "state their roles as 'Principal' and 'Attorney' respectively; "
            "include the relationship between them if provided"
        ),
        "leave_and_license": (
            "the opening clause identifying the Licensor (Owner) and Licensee (Occupant) — "
            "use the exact licensor_name, licensor_address, licensee_name, licensee_address from the FACTS; "
            "state their roles as 'Licensor' and 'Licensee' respectively"
        ),
        "gift_deed": (
            "the opening clause identifying the Donor and Donee (Recipient) — "
            "use the exact donor_name, donor_address, donee_name, donee_address from the FACTS; "
            "state their roles as 'Donor' and 'Donee' respectively"
        ),
        "conveyance_deed": (
            "the opening clause identifying the Conveyor (Developer/Transferor) and Transferee (Society/Buyer) — "
            "use the exact conveyor_name, conveyor_address, transferee_name, transferee_address from the FACTS; "
            "state their roles as 'Conveyor' and 'Transferee' respectively"
        ),
        "affidavit": (
            "the deponent identification clause — "
            "use the exact deponent_name, deponent_age, deponent_occupation, deponent_address from the FACTS; "
            "state that the deponent is making this affidavit on solemn affirmation"
        ),
    }

    # -----------------------------------------------------------------------
    # Document-type-specific RECITALS descriptions
    # -----------------------------------------------------------------------
    _RECITALS_BY_DOC: dict[str, str] = {
        "sale_deed": (
            "WHEREAS recitals establishing: (1) the vendor's ownership of the property by survey number, "
            "village, taluka, district; (2) the agreed consideration amount and payment mode; "
            "(3) any advance paid; (4) the execution date and registration office"
        ),
        "mortgage_deed": (
            "WHEREAS recitals establishing: (1) the mortgagor's ownership of the property; "
            "(2) the loan amount, interest rate, and repayment period from the FACTS; "
            "(3) the type of mortgage being created — state explicitly whether it is a "
            "Simple Mortgage under TPA Section 58(b), Equitable Mortgage under TPA Section 58(f), "
            "or English Mortgage under TPA Section 58(e), and include a brief recital explaining "
            "why this mortgage type is appropriate for the transaction — for Simple Mortgage: "
            "'the Mortgagor does not transfer possession and undertakes personal liability to repay'; "
            "for Equitable Mortgage: 'title deeds are deposited with the Mortgagee as security'; "
            "for English Mortgage: 'the Mortgagor transfers the property to the Mortgagee with a "
            "right of redemption on repayment'; "
            "(4) the mortgagee's agreement to advance the loan"
        ),
        "power_of_attorney": (
            "WHEREAS recitals establishing: (1) the principal's ownership or interest requiring representation; "
            "(2) the purpose for which the attorney is being appointed; "
            "(3) whether this is a general or special power of attorney; "
            "(4) the scope of authority being granted"
        ),
        "leave_and_license": (
            "WHEREAS recitals establishing: (1) the licensor's ownership of the licensed premises; "
            "(2) the licensee's request to use the premises; "
            "(3) the monthly license fee and security deposit from the FACTS; "
            "(4) the license period start and end dates"
        ),
        "gift_deed": (
            "WHEREAS recitals establishing: (1) the donor's ownership of the gifted property; "
            "(2) the donor's natural love and affection for the donee; "
            "(3) the donee's acceptance of the gift; "
            "(4) the date of possession delivery under TPA Section 122"
        ),
        "conveyance_deed": (
            "WHEREAS recitals establishing: (1) the developer's ownership and development of the project; "
            "(2) the MahaRERA registration number and OC status from the FACTS; "
            "(3) the society's formation and entitlement to conveyance; "
            "(4) the consideration amount"
        ),
        "affidavit": (
            "the deponent's declaration of facts — state each fact as a numbered paragraph; "
            "include the purpose of the affidavit (e.g. change of name, address proof, property ownership); "
            "use the exact facts from the FACTS section; "
            "end with 'I state that the above facts are true to the best of my knowledge and belief'"
        ),
    }

    # -----------------------------------------------------------------------
    # Document-type-specific ATTESTATION descriptions
    # -----------------------------------------------------------------------
    _ATTESTATION_BY_DOC: dict[str, str] = {
        "affidavit": (
            "the deponent's declaration formula and notarisation block — "
            "write EXACTLY this structure:\n"
            "  'Solemnly affirmed/sworn at [place_of_execution from FACTS] "
            "on [execution_date from FACTS].'\n"
            "  Signature line: 'Deponent: ___________________'\n"
            "  Notarisation block: 'Before me:'\n"
            "  '[notarisation_authority from FACTS]'\n"
            "  'Signature: ___________________'\n"
            "Do NOT use placeholder tokens like [Insert Name] or [Insert Registration Number]; "
            "use the exact notarisation_authority, place_of_execution, and execution_date values "
            "from the FACTS; if notary registration number is not in the FACTS write "
            "'[NOTARY REG. NO. — TO BE FILLED AT EXECUTION]'"
        ),
    }

    # -----------------------------------------------------------------------
    # Document-type-specific OPERATIVE_CLAUSE descriptions
    # -----------------------------------------------------------------------
    _OPERATIVE_BY_DOC: dict[str, str] = {
        "sale_deed": (
            "a substantive sale clause — include the consideration amount (exact value from FACTS), "
            "payment mode, encumbrance declaration, and title transfer under TPA Section 54; "
            "include stamp duty payment reference citing Maharashtra Stamp Act, 1958, Article 25 "
            "(Schedule I) for the applicable stamp duty rate on sale deeds"
        ),
        "mortgage_deed": (
            "a substantive mortgage clause — include the loan amount, interest rate, repayment period "
            "(all exact values from FACTS), and the type of mortgage under TPA Section 58 "
            "(simple, equitable, or English mortgage as applicable)"
        ),
        "power_of_attorney": (
            "a substantive authority clause — specify the exact acts the attorney is authorised to perform "
            "from the scope_of_authority field in FACTS; state whether revocable or irrevocable; "
            "include revocation conditions if provided"
        ),
        "leave_and_license": (
            "a substantive license clause — include the monthly license fee, security deposit, "
            "license period (all exact values from FACTS), and the licensee's obligation to vacate "
            "under Maharashtra Rent Control Act 1999 Section 24"
        ),
        "gift_deed": (
            "a substantive gift clause — state that the donor transfers the property out of natural love "
            "and affection without consideration; include donee acceptance; "
            "reference TPA Section 122 for gift requirements"
        ),
        "conveyance_deed": (
            "a substantive conveyance clause — include the consideration amount (exact value from FACTS), "
            "MahaRERA registration number, OC status, and the developer's obligation to convey "
            "under Maharashtra Ownership Flats Act"
        ),
        "affidavit": (
            "a substantive declaration clause — state the specific legal purpose of the affidavit "
            "(e.g. change of name, address proof, property ownership declaration); "
            "reference the Indian Evidence Act 1872 for sworn statement admissibility"
        ),
    }

    # -----------------------------------------------------------------------
    # Resolve description for this slot
    # -----------------------------------------------------------------------
    if slot_name == "PARTIES_CLAUSE":
        description = _PARTIES_BY_DOC.get(
            doc_type,
            "the opening clause identifying all parties to this deed — "
            "full names, roles (e.g. Vendor/Purchaser, Donor/Donee), and addresses",
        )
    elif slot_name == "RECITALS":
        description = _RECITALS_BY_DOC.get(
            doc_type,
            "WHEREAS recitals establishing the background facts, ownership history, "
            "and purpose of this deed",
        )
    elif slot_name == "SCHEDULE":
        description = (
            "the property schedule — EXACTLY 4-6 sentences describing: "
            "survey/gat number (exact value from FACTS), area with unit, "
            "four-direction boundaries (North/South/East/West) using actual adjacent survey numbers, "
            "road names, or named landmarks — do NOT use placeholder names like 'Shri X', 'Party A', "
            "or any generic stand-in; if boundary data is not in the FACTS write "
            "'[BOUNDARY NORTH — MANUAL ENTRY REQUIRED]' etc. for each missing direction; "
            "village, taluka, district. "
            "Do NOT repeat sentences. Stop after the boundary description."
        )
    elif slot_name == "ATTESTATION":
        description = _ATTESTATION_BY_DOC.get(
            doc_type,
            "the attestation/execution clause — IN WITNESS WHEREOF statement, "
            "signature lines for all parties and witnesses with exact execution_date and place from FACTS; "
            "include the Sub-Registrar office name and district where the deed is registered, "
            "e.g. 'before the Sub-Registrar, [Office Name], [District]'; "
            "use the registration_office field from FACTS if available",
        )
    elif slot_name.startswith("OPERATIVE_CLAUSE_"):
        n = slot_name.split("_")[-1]
        base = _OPERATIVE_BY_DOC.get(
            doc_type,
            "a substantive legal clause grounded in the legal sources below",
        )
        description = f"operative clause {n} — {base}"
    else:
        description = f"the {slot_name.replace('_', ' ').lower()} section of the deed"

    # -----------------------------------------------------------------------
    # Render fact pattern
    # -----------------------------------------------------------------------
    if isinstance(fact_pattern, dict) and "intake" in fact_pattern:
        intake = fact_pattern.get("intake", {})
        ocr_fields = fact_pattern.get("ocr_fields", {})
        lines = [f"  {k}: {v}" for k, v in intake.items() if v not in (None, "", [])]
        lines += [f"  {k}: {v}" for k, v in ocr_fields.items() if v not in (None, "", [])]
        fact_block = "FACTS:\n" + "\n".join(lines)
    elif isinstance(fact_pattern, dict):
        fact_block = "FACTS:\n" + json.dumps(fact_pattern, indent=2, ensure_ascii=False)
    else:
        fact_block = f"FACTS:\n{fact_pattern}"

    # -----------------------------------------------------------------------
    # Citation instruction (not for SCHEDULE or ATTESTATION)
    # -----------------------------------------------------------------------
    citation_instruction = ""
    if slot_name not in ("SCHEDULE", "ATTESTATION"):
        example = _build_citation_examples(cache_entries or [])
        citation_instruction = (
            "\nAfter the clause text, add one inline citation marker in EXACTLY this format:\n"
            "  [CITE:Act Name, Year, Section X(Y)]\n"
            "Example: [CITE:Transfer of Property Act, 1882, Section 54]\n"
            "Replace 'Act Name', 'Year', and 'Section X(Y)' with the actual values from the legal sources below.\n"
            "If no citation applies, write: [UNGROUNDED — MANUAL REVIEW REQUIRED]\n"
        )

    # SCHEDULE gets a strict length cap to prevent repetition
    length_rule = "- Write 2-6 sentences of real legal text. No headings, no labels, no preamble."
    if slot_name == "SCHEDULE":
        length_rule = (
            "- Write EXACTLY 4-6 sentences. Stop immediately after the West boundary. "
            "Do NOT repeat any sentence. No headings, no labels, no preamble."
        )

    return f"""You are a Maharashtra legal document drafting assistant.
Write ONLY the content for the {slot_name} section of a {doc_type_display}.

SECTION TO WRITE: {description}

RULES:
{length_rule}
- Use exact values from the FACTS below — do not invent names, amounts, or dates.
- Apply Maharashtra jurisdiction throughout.
- Wrap every party name (Vendor, Purchaser, Mortgagor, Mortgagee, Principal, Attorney, Licensor, Licensee, Donor, Donee, Conveyor, Transferee, Deponent) in <UNDERLINE>...</UNDERLINE> tags each time it appears.{citation_instruction}
{fact_block}

LEGAL SOURCES (cite only from these):
{cache_context}

Write the {slot_name} content now:"""



def _assemble_from_slots(
    slot_contents: dict[str, str],
    template_slots: list[str],
    doc_type: str,
    cache_entries: list | None = None,
) -> str:
    """
    Pass 2: assemble the final document body from per-slot content.

    Renders each slot with a clean section heading followed by its content,
    in the order defined by template_slots. Also applies a post-processing
    pass to resolve any residual [Legislature/Court] / [Year] placeholders
    that the LLM failed to fill in.
    """
    doc_type_display = doc_type.replace("_", " ").title().upper()

    # Human-readable headings for each slot
    def _heading(slot_name: str) -> str:
        if slot_name == "PARTIES_CLAUSE":
            return "PARTIES"
        if slot_name == "RECITALS":
            return "RECITALS"
        if slot_name == "SCHEDULE":
            return "SCHEDULE OF PROPERTY"
        if slot_name == "ATTESTATION":
            return "ATTESTATION"
        if slot_name.startswith("OPERATIVE_CLAUSE_"):
            n = slot_name.split("_")[-1]
            return f"OPERATIVE CLAUSE {n}"
        return slot_name.replace("_", " ")

    parts: list[str] = [f"THIS {doc_type_display}\n"]
    for slot_name in template_slots:
        content = slot_contents.get(slot_name, "").strip()
        if not content:
            content = "[UNGROUNDED — MANUAL REVIEW REQUIRED]"
        parts.append(f"{_heading(slot_name)}\n\n{content}")

    body = "\n\n".join(parts)

    # -----------------------------------------------------------------------
    if cache_entries:
        body = _resolve_citation_placeholders(body, cache_entries)

    # Replace citations missing a section number with [UNGROUNDED]
    # Catches [Act Name, Year] tokens NOT followed by "Section" — these are
    # unverifiable act-only citations that the LLM produced without a section.
    _CITATION_NO_SECTION_RE = re.compile(
        r"\[([^\]]+?,\s*\d{4})\](?!\s+Section)",
        re.IGNORECASE,
    )
    body = _CITATION_NO_SECTION_RE.sub("[UNGROUNDED — MANUAL REVIEW REQUIRED]", body)

    return body


def _resolve_citation_placeholders(text: str, cache_entries: list) -> str:
    """
    Replace residual [Legislature/Court] and [Year] placeholder tokens in
    citation strings that the LLM failed to fill in.

    Matches citations of the form:
        [Act Name, Year] Section X(Y), [Legislature/Court], [Year]
    and replaces the bracketed placeholders with the correct values derived
    from the cache entry metadata.

    Also handles:
    - Unbracketed court/year tokens (e.g. Legislature/Court, Year)
    - Mixed bracketed/unbracketed forms
    - Citations missing court+year entirely (second-pass regex)
    """
    _CENTRAL_ACTS = {
        "transfer of property act", "registration act", "indian evidence act",
        "indian contract act", "indian stamp act", "code of civil procedure",
        "limitation act", "specific relief act",
        "real estate regulation and development act", "rera",
    }
    _MAHARASHTRA_ACTS = {
        "maharashtra rent control act", "maharashtra land revenue code", "mlrc",
        "maharashtra co-operative societies act", "maharashtra ownership flats act",
        "maharashtra regional and town planning act",
    }

    # Build lookup: normalised act name -> (court_string, year_string)
    lookup: dict[str, tuple[str, str]] = {}
    for entry in cache_entries:
        name: str = entry.name
        name_lower = name.lower()
        year_match = re.search(r"\b(1[89]\d{2}|20\d{2})\b", name)
        year = year_match.group(1) if year_match else ""
        if any(k in name_lower for k in _CENTRAL_ACTS):
            court = "Parliament of India"
        elif "judgment" in name_lower or "bhc" in name_lower:
            court = "Bombay High Court"
        else:
            court = "Parliament of India"
        # Key on the act name without the year suffix for flexible matching
        key = re.sub(r",?\s*\d{4}$", "", name_lower).strip()
        lookup[key] = (court, year)

    def _lookup_act(act_ref: str) -> tuple[str, str]:
        """Find best matching (court, year) for an act reference string."""
        act_key = re.sub(r",?\s*\d{4}$", "", act_ref.lower()).strip()
        for key, (c, y) in lookup.items():
            if key in act_key or act_key in key:
                return c, y
        return "Parliament of India", ""

    def _replace_placeholders(m: re.Match) -> str:
        act_ref = m.group(1).lower()  # e.g. "registration act, 1908"
        section = m.group(2)          # e.g. "Section 17(1)(b)"
        court_token = m.group(3)      # "[Legislature/Court]" or already filled
        year_token = m.group(4)       # "[Year]" or already filled

        # Detect placeholder court tokens — includes unbracketed literal variants
        needs_court = court_token.strip("[]").lower() in (
            "legislature/court", "court", "legislature", "legislature / court",
            "[legislature/court]",
        )
        needs_year = year_token.strip("[]").lower() == "year"

        if not (needs_court or needs_year):
            return m.group(0)  # already filled, leave as-is

        court, year = _lookup_act(act_ref)

        resolved_court = court if needs_court else court_token
        resolved_year  = year  if (needs_year and year) else year_token

        return f"[{m.group(1)}] {section}, {resolved_court}, {resolved_year}"

    # Pattern: [Act Name, Year] Section X(Y), [Legislature/Court], [Year]
    # Handles bracketed and unbracketed court/year tokens
    pattern = re.compile(
        r"\[([^\]]+)\]\s+(Section\s+[\w()./]+),\s*(\[[^\]]+\]|[\w\s]+),\s*(\[[^\]]+\]|\d{4})",
        re.IGNORECASE,
    )
    text = pattern.sub(_replace_placeholders, text)

    # Second pass: catch citations missing court+year entirely
    # Matches: [Act Name, Year] Section X(Y)  — NOT followed by a comma
    _CITATION_MISSING_COURT_RE = re.compile(
        r"(\[([^\]]+?,\s*\d{4})\]\s+Section\s+[\w()./]+)(?!\s*,)",
        re.IGNORECASE,
    )

    def _append_missing_court(m: re.Match) -> str:
        full_match = m.group(1)
        act_ref = m.group(2)
        court, year = _lookup_act(act_ref)
        if year:
            return f"{full_match}, {court}, {year}"
        return full_match

    text = _CITATION_MISSING_COURT_RE.sub(_append_missing_court, text)

    # Normalise common court name abbreviations the LLM may write
    _COURT_NORMALISATION = [
        (re.compile(r"\bParliament\b(?!\s+of\s+India)", re.IGNORECASE), "Parliament of India"),
        (re.compile(r"\bMaharashtra\s+Legis\b", re.IGNORECASE), "Maharashtra Legislature"),
        (re.compile(r"\bBombay\s+HC\b", re.IGNORECASE), "Bombay High Court"),
        (re.compile(r"\bBHC\b"), "Bombay High Court"),
    ]
    for norm_re, replacement in _COURT_NORMALISATION:
        text = norm_re.sub(replacement, text)

    return text


def _build_generation_prompt(
    fact_pattern: dict | str,
    cache_context: str,
    doc_type: str,
    template_slots: list[str] | None = None,
) -> str:
    """
    Build the full LLM prompt for legal document generation.
    The cache context is injected as the sole grounding source.

    When fact_pattern is a dict with an "intake" key (enriched fact pattern
    from the guided intake system), the prompt renders each intake field
    explicitly with its value and instructs the LLM to use exact values.

    Backward compatible: plain string or dict without "intake" key falls
    back to the original JSON serialisation behaviour.

    Args:
        template_slots: Optional list of slot names (e.g. ["PARTIES_CLAUSE",
                        "RECITALS", "OPERATIVE_CLAUSE_1", ...]) extracted from
                        the document template. When provided, the prompt
                        explicitly instructs the LLM to produce content for
                        each slot so no placeholder is left unfilled.
    """
    doc_type_display = doc_type.replace("_", " ").title()

    if isinstance(fact_pattern, dict) and "intake" in fact_pattern:
        # --- Enriched intake fact pattern ---
        intake: dict = fact_pattern.get("intake", {})
        ocr_fields: dict = fact_pattern.get("ocr_fields", {})

        intake_lines = "\n".join(
            f"  {k}: {v}" for k, v in intake.items() if v not in (None, "", [])
        )
        ocr_lines = "\n".join(
            f"  {k}: {v}" for k, v in ocr_fields.items() if v not in (None, "", [])
        )

        fact_block = (
            "FACT PATTERN (use these exact values in the document — "
            "do not invent or substitute any of these facts):\n"
        )
        if intake_lines:
            fact_block += f"\nIntake answers:\n{intake_lines}\n"
        if ocr_lines:
            fact_block += f"\nOCR-extracted fields:\n{ocr_lines}\n"

    elif isinstance(fact_pattern, dict):
        fact_str = json.dumps(fact_pattern, indent=2, ensure_ascii=False)
        fact_block = f"FACT PATTERN:\n{fact_str}"
    else:
        fact_block = f"FACT PATTERN:\n{fact_pattern}"

    # Build the template slot instruction block when slots are available.
    # This is the key fix: the LLM is told exactly which sections to produce
    # and that it must write real content — never leave a placeholder name.
    if template_slots:
        slot_lines = "\n".join(f"  {i+1}. {name}" for i, name in enumerate(template_slots))
        slot_block = (
            f"\nDOCUMENT SECTIONS TO WRITE (write actual content for EVERY section below — "
            f"do NOT output the section name as a placeholder, write the real legal text):\n"
            f"{slot_lines}\n"
        )
    else:
        slot_block = ""

    return f"""You are a Maharashtra legal document drafting assistant.
Your task is to draft a {doc_type_display} based ONLY on the legal sources provided below.

INSTRUCTIONS:
1. Draft a concise but complete {doc_type_display} (1–2 pages) using the fact pattern provided.
2. Every clause MUST be grounded in one of the legal sources below.
3. For each clause, insert an inline citation in the format:
   [Act Name, Year] Section X(Y), [Legislature/Court], [Year]
4. If a clause cannot be grounded in the provided sources, insert:
   [UNGROUNDED — MANUAL REVIEW REQUIRED]
5. Apply Maharashtra-specific statutes and jurisdiction throughout.
6. Be concise — do not repeat clauses or pad with boilerplate.
7. CRITICAL: Write real legal text for every section. Do NOT output bare section names
   like PARTIES_CLAUSE or ATTESTATION as placeholders — replace them with actual content.
{slot_block}
{fact_block}

LEGAL SOURCES (use ONLY these for citations):
{cache_context}

Now draft the {doc_type_display}:
"""


# ---------------------------------------------------------------------------
# Citation extraction
# ---------------------------------------------------------------------------

# Matches: [Act Name, Year] Section X(Y), [Legislature], [Year]
_CITATION_RE = re.compile(
    r"\[([^\]]+?,\s*\d{4})\]\s+Section\s+([\w\(\)\.]+),\s*\[([^\]]+)\],\s*\[?(\d{4})\]?",
    re.IGNORECASE,
)

_UNGROUNDED_RE = re.compile(
    r"\[UNGROUNDED[^\]]*\]",
    re.IGNORECASE,
)


def _norm_act(s: str) -> str:
    """Normalise an act name for fuzzy matching: lowercase, strip punctuation."""
    return re.sub(r"[,\.\-]+", " ", s.lower()).strip()


def _extract_citations(content: str, cache: LegalCache) -> tuple[list[Citation], list[int]]:
    """
    Parse inline citations from the generated content.
    Returns (citations, ungrounded_clause_indices).

    A "clause" is approximated as a numbered paragraph or sentence.
    """
    citations: list[Citation] = []
    ungrounded_indices: list[int] = []

    # Split into clauses by numbered lines or double newlines
    clauses = re.split(r"\n\s*\n|\n(?=\d+\.)", content)

    # Build a set of known document names for grounding verification
    known_names = {doc.name.lower() for doc in cache.documents}

    for idx, clause in enumerate(clauses):
        clause_is_ungrounded = bool(_UNGROUNDED_RE.search(clause))

        for m in _CITATION_RE.finditer(clause):
            act_year = m.group(1).strip()   # e.g. "Transfer of Property Act, 1882"
            section = m.group(2).strip()
            court = m.group(3).strip()
            year = m.group(4).strip()

            act_norm = _norm_act(act_year)
            grounded = any(
                _norm_act(name) in act_norm or act_norm in _norm_act(name)
                for name in known_names
            )

            if grounded:
                source_doc = next(
                    (
                        doc.name for doc in cache.documents
                        if _norm_act(doc.name) in act_norm or act_norm in _norm_act(doc.name)
                    ),
                    act_year,
                )
                citations.append(Citation(
                    act_name=act_year,
                    section=section,
                    year=year,
                    clause_index=idx,
                    source_document=source_doc,
                ))
            else:
                logger.warning(
                    "Citation '%s' not found in loaded cache — marking clause %d as ungrounded.",
                    act_year, idx,
                )
                ungrounded_indices.append(idx)

        if clause_is_ungrounded and idx not in ungrounded_indices:
            ungrounded_indices.append(idx)

    return citations, list(set(ungrounded_indices))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_cache(document_type: str, llm_backend: str) -> LegalCache:
    """
    Load the Legal Cache for the given document type and LLM backend.

    - Reads the YAML manifest from config/cache_manifests/{document_type}.yaml
    - Enforces 90% token budget (drops low-priority entries first)
    - Writes a session log entry
    - Returns a LegalCache with a new session_id

    The returned LegalCache is stored in the session registry so that
    subsequent generate_draft() calls within the same session can reuse it.

    Args:
        document_type: e.g. "sale_deed"
        llm_backend:   e.g. "llama3_8b"

    Returns:
        LegalCache instance.
    """
    if llm_backend not in BACKEND_MODEL_TAGS:
        raise ValueError(
            f"Unsupported LLM backend '{llm_backend}'. "
            f"Supported: {list(BACKEND_MODEL_TAGS.keys())}"
        )

    result: ManifestLoadResult = load_manifest(document_type, llm_backend)

    session_id = str(uuid.uuid4())

    cache = LegalCache(
        documents=result.entries,
        total_tokens=result.total_tokens,
        llm_backend=llm_backend,
        session_id=session_id,
        omitted_documents=result.omitted,
        document_type=document_type,
    )

    # Register in session store
    _session_cache[session_id] = cache

    # Write session log (Requirement 3.6)
    _write_session_log(cache)

    logger.info(
        "Loaded cache for document_type='%s', backend='%s', "
        "session_id=%s, total_tokens=%d, omitted=%s",
        document_type, llm_backend, session_id,
        result.total_tokens, result.omitted,
    )

    return cache


def generate_draft(
    fact_pattern: dict | str,
    cache: LegalCache,
    doc_type: str,
    timeout: int = 900,
    template_slots: list[str] | None = None,
    slot_progress_cb: Callable[[int, str], None] | None = None,
) -> DraftResult:
    """
    Generate a legal document draft using the pre-loaded Legal Cache.

    - Reuses the provided LegalCache without reloading (Requirement 4.8)
    - Calls the Ollama HTTP API with the assembled prompt
    - Extracts inline citations and identifies ungrounded clauses
    - Returns a DraftResult with content, citations, ungrounded_clauses, session_id

    Args:
        fact_pattern:       Free-text or structured JSON dict of facts.
        cache:              LegalCache returned by load_cache().
        doc_type:           Document type string, e.g. "sale_deed".
        template_slots:     Optional list of slot names from the document template.
        slot_progress_cb:   Optional callback(slot_index, slot_name) called before
                            each slot LLM call — used by the frontend to update the
                            progress bar during multi-slot generation.

    Returns:
        DraftResult instance.
    """
    model_tag = BACKEND_MODEL_TAGS.get(cache.llm_backend)
    if model_tag is None:
        raise ValueError(
            f"Unknown LLM backend in cache: '{cache.llm_backend}'"
        )

    cache_context = _build_cache_context(
        cache,
        max_chars=3000 if cache.llm_backend in _GROQ_BACKENDS else 24000,
    )

    # -----------------------------------------------------------------------
    # Two-pass generation when template_slots are available
    # -----------------------------------------------------------------------
    if template_slots:
        logger.info(
            "Two-pass generation: %d slots for doc_type='%s', session_id=%s",
            len(template_slots), doc_type, cache.session_id,
        )
        slot_contents: dict[str, str] = {}
        for i, slot_name in enumerate(template_slots):
            # Fire progress callback before each slot so the frontend bar advances.
            if slot_progress_cb is not None:
                try:
                    slot_progress_cb(i, slot_name)
                except Exception:
                    pass  # never let a UI callback break generation

            # Small inter-slot delay for Groq to avoid burst rate limiting.
            # 0.5s is sufficient — the exponential backoff in _call_groq handles
            # any 429s if they occur. 2s was overly conservative.
            if i > 0 and cache.llm_backend in _GROQ_BACKENDS:
                time.sleep(0.5)
            slot_prompt = _build_slot_prompt(slot_name, fact_pattern, cache_context, doc_type, cache.documents)
            logger.debug("Pass 1 — generating slot '%s'", slot_name)
            if cache.llm_backend in _GROQ_BACKENDS:
                slot_text = _call_groq(model_tag, slot_prompt, timeout=60)
            else:
                slot_text = _call_ollama(model_tag, slot_prompt, timeout=timeout)
            slot_text = slot_text.strip()

            # Per-slot retry: if the LLM returned fewer tokens than the minimum
            # for this slot type, retry once with a more explicit prompt.
            from src.evaluation.evaluator import (
                _MIN_SLOT_TOKENS_BY_TYPE,
                _MIN_SLOT_TOKENS_DEFAULT,
            )
            min_tokens = _MIN_SLOT_TOKENS_BY_TYPE.get(slot_name, _MIN_SLOT_TOKENS_DEFAULT)
            if len(slot_text.split()) < min_tokens:
                logger.warning(
                    "Slot '%s' returned only %d tokens (min %d) — retrying once.",
                    slot_name, len(slot_text.split()), min_tokens,
                )
                if cache.llm_backend in _GROQ_BACKENDS:
                    time.sleep(2)
                retry_prompt = (
                    slot_prompt
                    + f"\n\nIMPORTANT: Your previous response was too short. "
                    f"Write at least {min_tokens} words of substantive legal content "
                    f"for the {slot_name} section. Do not repeat the heading."
                )
                if cache.llm_backend in _GROQ_BACKENDS:
                    slot_text = _call_groq(model_tag, retry_prompt, timeout=60).strip()
                else:
                    slot_text = _call_ollama(model_tag, retry_prompt, timeout=timeout).strip()

            slot_contents[slot_name] = slot_text

        raw_content = _assemble_from_slots(slot_contents, template_slots, doc_type, cache.documents)
        logger.info("Pass 2 — assembled %d slots into document body", len(template_slots))
    else:
        # Single-pass fallback (no template slots provided)
        prompt = _build_generation_prompt(fact_pattern, cache_context, doc_type, None)
        if cache.llm_backend in _GROQ_BACKENDS:
            raw_content = _call_groq(model_tag, prompt, timeout=60)
        else:
            raw_content = _call_ollama(model_tag, prompt, timeout=timeout)

    citations, ungrounded_clauses = _extract_citations(raw_content, cache)

    logger.info(
        "Draft generated: %d citations, %d ungrounded clauses, session_id=%s",
        len(citations), len(ungrounded_clauses), cache.session_id,
    )

    return DraftResult(
        content=raw_content,
        citations=citations,
        ungrounded_clauses=ungrounded_clauses,
        session_id=cache.session_id,
    )


def get_session_cache(session_id: str) -> LegalCache | None:
    """
    Retrieve a previously loaded LegalCache by session ID.
    Returns None if the session is not found.
    """
    return _session_cache.get(session_id)


def clear_session(session_id: str) -> None:
    """Remove a session from the registry (e.g. on session expiry)."""
    _session_cache.pop(session_id, None)
