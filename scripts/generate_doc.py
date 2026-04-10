"""
Generate a Sale Deed using Llama 3.1 8B (Groq default) with the full
Phase 1.5 citation + formatting pipeline, exported to PDF and DOCX.

Output: output/individual/sale_deed_llama8b_<run_id>.pdf
        output/individual/sale_deed_llama8b_<run_id>.docx

Usage:
    python -m scripts.generate_doc
"""
from __future__ import annotations

import re
import sys
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from src.cag.engine import (
    BACKEND_MODEL_TAGS,
    _call_groq,
    _build_cache_context,
    _build_slot_prompt,
    load_cache,
)
from src.generation.citation_formatter import (
    assign_reference_numbers,
    build_citation_index,
    collect_citations,
    replace_markers,
)
from src.generation.formatting_engine import get_underline_ranges
from src.generation.document_generator import GeneratedDocument
from src.generation.exporter import export_document

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OUTPUT_DIR = "output/individual"
DOC_TYPE   = "sale_deed"
BACKEND    = "groq_llama3_8b"          # Llama 3.1 8B Instant — high rate limit
MODEL_TAG  = BACKEND_MODEL_TAGS[BACKEND]
RUN_ID     = str(uuid.uuid4())[:8]

FACT_PATTERN = {
    "intake": {
        "seller_name":         "Shri Vikram Anand Kulkarni",
        "seller_address":      "Plot No. 7, Saraswati Nagar, Nashik - 422 005, Maharashtra",
        "buyer_name":          "Smt Deepa Suresh Joshi",
        "buyer_address":       "Flat 3C, Laxmi Apartments, Aurangabad - 431 001, Maharashtra",
        "consideration_amount":"72,00,000",
        "payment_mode":        "Bank Transfer (NEFT)",
        "advance_amount":      "7,20,000",
        "survey_number":       "456/2B",
        "village":             "Satpur",
        "taluka":              "Nashik",
        "district":            "Nashik",
        "area":                "950 sq. ft.",
        "execution_date":      "10th April 2026",
        "registration_office": "Sub-Registrar Office, Nashik",
    }
}

SLOTS = [
    "PARTIES_CLAUSE",
    "RECITALS",
    "OPERATIVE_CLAUSE_1",
    "OPERATIVE_CLAUSE_2",
    "SCHEDULE",
    "ATTESTATION",
]

# Strip ALL <UNDERLINE> tag remnants from LLM output — the 8B model produces
# corrupted fragments like LINE>, INE>, ERLINE> that break the formatter.
# We always use the heuristic detector (regex-based) which is reliable.
_UNDERLINE_STRIP = re.compile(
    r"</?UNDERLINE>|</?underline>|</?[Uu][Nn][Dd][Ee][Rr][Ll][Ii][Nn][Ee]>"  # clean tags
    r"|(?:</?|</?)(?:[A-Z]*LINE>|[A-Z]*RLINE>|[A-Z]*ERLINE>|[A-Z]*DERLINE>)" # corrupted suffixes
    r"|LINE>|RLINE>|ERLINE>|DERLINE>|NDERLINE>",                               # bare suffixes
    re.IGNORECASE,
)

def _strip_underline_markers(text: str) -> str:
    """Remove all UNDERLINE tag variants and corrupted remnants from LLM output."""
    return _UNDERLINE_STRIP.sub("", text)


# ---------------------------------------------------------------------------
# Slot generation
# ---------------------------------------------------------------------------

def generate_slot(slot_name: str, cache) -> str:
    # 8B model has a tighter context window — cap cache at 6000 chars
    cache_context = _build_cache_context(cache, max_chars=6000)
    prompt = _build_slot_prompt(
        slot_name=slot_name,
        fact_pattern=FACT_PATTERN,
        cache_context=cache_context,
        doc_type=DOC_TYPE,
        cache_entries=cache.documents,
    )
    print(f"  → {slot_name:<25}", end=" ", flush=True)
    t0 = time.perf_counter()
    text = _call_groq(MODEL_TAG, prompt, timeout=60)
    print(f"{(time.perf_counter()-t0)*1000:>6.0f}ms")
    return _strip_underline_markers(text)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 70)
    print(f"  Sale Deed  |  {MODEL_TAG}  |  run_id={RUN_ID}")
    print("=" * 70)

    # 1. Cache
    print("\n[1/5] Loading legal cache ...")
    cache = load_cache(DOC_TYPE, BACKEND)
    print(f"      {len(cache.documents)} documents, {cache.total_tokens} tokens")

    # 2. Generate slots
    print(f"\n[2/5] Generating {len(SLOTS)} slots ...")
    body = "\n\n".join(generate_slot(s, cache) for s in SLOTS)

    # 3. Citation pipeline
    print("\n[3/5] Citation formatting ...")
    t0 = time.perf_counter()
    _, markers   = collect_citations(body)
    ref_map      = assign_reference_numbers(markers)
    body         = replace_markers(body, ref_map)
    citation_idx = build_citation_index(markers)
    c_ms = (time.perf_counter() - t0) * 1000
    print(f"      {len(ref_map)} unique citation(s)  —  {c_ms:.1f}ms")

    # 4. Underlining
    print("\n[4/5] Underlining pipeline ...")
    t0 = time.perf_counter()
    ranges = get_underline_ranges(body, DOC_TYPE, parties_only=False)
    u_ms = (time.perf_counter() - t0) * 1000
    print(f"      {len(ranges)} underline range(s)  —  {u_ms:.1f}ms")

    # 5. Export
    print(f"\n[5/5] Exporting to {OUTPUT_DIR}/ ...")

    header = (
        f"MAHARASHTRA LEGAL DOCUMENT GENERATION SYSTEM — Phase 1.5\n"
        f"Model: {MODEL_TAG} (Groq)  |  Doc type: {DOC_TYPE}  |  Run ID: {RUN_ID}\n"
        f"DISCLAIMER: AI-generated draft. Review by a qualified legal professional before use."
    )

    # Embed citation index into body so PDF renderer sees plain paragraphs
    full_body = body + "\n\n" + citation_idx

    doc = GeneratedDocument(
        content=f"{header}\n\n{full_body}",
        header=header,
        body=full_body,
        citation_index="",      # already in body
        citations=[],
        ungrounded_clauses=[],
        high_hallucination_risk=False,
        run_id=RUN_ID,
        unfilled_slots=[],
        doc_type=DOC_TYPE,
        pipeline_variant="CAG-Phase1.5",
    )

    base = f"sale_deed_llama8b_{RUN_ID}"
    result = export_document(doc, OUTPUT_DIR, base_filename=base)

    # Summary
    print("\n" + "=" * 70)
    print("OUTPUT")
    print("=" * 70)
    if result.pdf_path:
        print(f"  PDF  : {result.pdf_path}")
    if result.docx_path:
        print(f"  DOCX : {result.docx_path}")
    for err in result.errors:
        print(f"  ERROR: {err}")

    print(f"\n  Citation latency : {c_ms:.1f}ms  ({'✓' if c_ms<=50 else '✗'} ≤50ms)")
    print(f"  Underline latency: {u_ms:.1f}ms  ({'✓' if u_ms<=100 else '✗'} ≤100ms)")

    sys.exit(0 if not result.errors else 1)


if __name__ == "__main__":
    main()
