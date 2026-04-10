"""
Generate a Sale Deed using Llama 3.3 70B (Groq), run the full Phase 1.5
citation + formatting pipeline, and export to PDF + DOCX.

Output: output/individual/sale_deed_llama70b.pdf
        output/individual/sale_deed_llama70b.docx

Usage:
    python -m scripts.generate_llama70b_doc
"""
from __future__ import annotations

import os
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
from src.generation.formatting_engine import get_underline_ranges, underline_parties_pdf_html
from src.generation.document_generator import GeneratedDocument
from src.generation.exporter import export_document

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OUTPUT_DIR = "output/individual"
DOC_TYPE = "sale_deed"
BACKEND = "groq_llama3_70b"
MODEL_TAG = BACKEND_MODEL_TAGS[BACKEND]
RUN_ID = str(uuid.uuid4())[:8]

FACT_PATTERN = {
    "intake": {
        "seller_name": "Shri Vikram Anand Kulkarni",
        "seller_address": "Plot No. 7, Saraswati Nagar, Nashik - 422 005, Maharashtra",
        "buyer_name": "Smt Deepa Suresh Joshi",
        "buyer_address": "Flat 3C, Laxmi Apartments, Aurangabad - 431 001, Maharashtra",
        "consideration_amount": "72,00,000",
        "payment_mode": "Bank Transfer (NEFT)",
        "advance_amount": "7,20,000",
        "survey_number": "456/2B",
        "village": "Satpur",
        "taluka": "Nashik",
        "district": "Nashik",
        "area": "950 sq. ft.",
        "execution_date": "10th April 2026",
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

# Normalise both <UNDERLINE> and <underline> to our canonical form before processing
_UNDERLINE_NORM = re.compile(r"</?underline>", re.IGNORECASE)


def _normalise_markers(text: str) -> str:
    """Replace any case variant of <underline> tags with canonical <UNDERLINE>."""
    def _fix(m: re.Match) -> str:
        raw = m.group(0)
        if raw.startswith("</"):
            return "</UNDERLINE>"
        return "<UNDERLINE>"
    return _UNDERLINE_NORM.sub(_fix, text)


# ---------------------------------------------------------------------------
# Slot generation
# ---------------------------------------------------------------------------

def generate_slot(slot_name: str, cache) -> str:
    cache_context = _build_cache_context(cache)
    prompt = _build_slot_prompt(
        slot_name=slot_name,
        fact_pattern=FACT_PATTERN,
        cache_context=cache_context,
        doc_type=DOC_TYPE,
        cache_entries=cache.documents,
    )
    print(f"  → {slot_name:<25}", end=" ", flush=True)
    t0 = time.perf_counter()
    result = _call_groq(MODEL_TAG, prompt, timeout=60)
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"{elapsed:>6.0f}ms")
    return _normalise_markers(result)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 70)
    print(f"  Sale Deed — Llama 3.3 70B (Groq)   run_id={RUN_ID}")
    print("=" * 70)

    # 1. Load cache
    print("\n[1/5] Loading legal cache ...")
    cache = load_cache(DOC_TYPE, BACKEND)
    print(f"      {len(cache.documents)} documents, {cache.total_tokens} tokens")

    # 2. Generate slots
    print(f"\n[2/5] Generating {len(SLOTS)} slots via {MODEL_TAG} ...")
    slot_texts: dict[str, str] = {}
    for slot in SLOTS:
        slot_texts[slot] = generate_slot(slot, cache)

    # Assemble body (clean slot headers for the final doc)
    body = "\n\n".join(slot_texts[s] for s in SLOTS)

    # 3. Citation pipeline
    print("\n[3/5] Citation formatting ...")
    t0 = time.perf_counter()
    _, markers = collect_citations(body)
    ref_map = assign_reference_numbers(markers)
    body_with_refs = replace_markers(body, ref_map)
    citation_index_text = build_citation_index(markers)
    citation_ms = (time.perf_counter() - t0) * 1000
    print(f"      {len(ref_map)} unique citation(s)  —  {citation_ms:.1f}ms")

    # 4. Underlining (for PDF pre-render)
    print("\n[4/5] Underlining pipeline ...")
    t0 = time.perf_counter()
    full_body = body_with_refs
    ranges = get_underline_ranges(full_body, DOC_TYPE)
    underline_ms = (time.perf_counter() - t0) * 1000
    print(f"      {len(ranges)} underline range(s)  —  {underline_ms:.1f}ms")

    # 5. Build GeneratedDocument and export
    print(f"\n[5/5] Exporting to {OUTPUT_DIR}/ ...")

    header_text = (
        f"MAHARASHTRA LEGAL DOCUMENT GENERATION SYSTEM — Phase 1.5\n"
        f"Model: {MODEL_TAG} (Groq)  |  Doc type: {DOC_TYPE}  |  Run ID: {RUN_ID}\n"
        f"DISCLAIMER: AI-generated draft. Review by a qualified legal professional before use."
    )

    # Embed the citation index into the body so the PDF renderer
    # sees it as plain paragraphs (avoids broken AnchorFlowable links
    # that require a populated citations list).
    body_with_index = full_body + "\n\n" + citation_index_text

    doc = GeneratedDocument(
        content=f"{header_text}\n\n{body_with_index}",
        header=header_text,
        body=body_with_index,
        citation_index="",      # already embedded in body
        citations=[],
        ungrounded_clauses=[],
        high_hallucination_risk=False,
        run_id=RUN_ID,
        unfilled_slots=[],
        doc_type=DOC_TYPE,
        pipeline_variant="CAG-Phase1.5",
    )

    base_filename = f"sale_deed_llama70b_{RUN_ID}"
    result = export_document(doc, OUTPUT_DIR, base_filename=base_filename)

    # Summary
    print("\n" + "=" * 70)
    print("OUTPUT FILES")
    print("=" * 70)
    if result.pdf_path:
        print(f"  PDF  : {result.pdf_path}")
    if result.docx_path:
        print(f"  DOCX : {result.docx_path}")
    if result.errors:
        for err in result.errors:
            print(f"  ERROR: {err}")

    print("\nLATENCY")
    print(f"  Citation formatting : {citation_ms:.1f}ms  "
          f"({'✓' if citation_ms <= 50 else '✗'} ≤50ms)")
    print(f"  Underlining         : {underline_ms:.1f}ms  "
          f"({'✓' if underline_ms <= 100 else '✗'} ≤100ms)")

    sys.exit(0 if not result.errors else 1)


if __name__ == "__main__":
    main()
