#!/usr/bin/env python3
"""
scripts/test_ragas_integration.py

Standalone RAGAS Tier 3 validation script.

Runs evaluate_ragas() on the sale deed document (same one used in
evaluate_single_pdf.py) to confirm the full Groq-judged evaluation
pipeline works before wiring it into the broader eval suite.

Usage:
    python scripts/test_ragas_integration.py

Requirements:
    - GROQ_RAGAS_API_KEY or GROQ_API_KEY set in .env
    - pip install ragas sentence-transformers langchain-community

Output:
    Console report + output/ragas_test_result.json
"""
from __future__ import annotations

import json
import sys
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Mock objects — replicate GeneratedDocument and LegalCache shapes
# so we can test evaluate_ragas() without running the full pipeline
# ---------------------------------------------------------------------------

@dataclass
class _MockCacheEntry:
    name: str
    content: str
    tokens: int = 500
    priority: str = "normal"
    path: str = ""
    section_hint: str = ""


@dataclass
class _MockCache:
    """Minimal stand-in for LegalCache."""
    documents: list
    total_tokens: int = 0
    llm_backend: str = "groq_llama3_8b"
    session_id: str = "test-session"
    omitted_documents: list = field(default_factory=list)
    document_type: str = "sale_deed"


@dataclass
class _MockDoc:
    """Minimal stand-in for GeneratedDocument."""
    body: str
    run_id: str = "7c040c68-a430-4009-b405-d92a28705728"
    doc_type: str = "sale_deed"
    pipeline_variant: str = "CAG"
    citations: list = field(default_factory=list)
    ungrounded_clauses: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sale deed document body (extracted from the test PDF)
# ---------------------------------------------------------------------------

SALE_DEED_BODY = """
THIS SALE DEED

PARTIES

Eric Siq, the Vendor, residing at Kiran Samruddhi B, Sus Gaon, Pune, and Abhav Bhanot,
the Purchaser, residing at Balaji Whitefield, Sus Gaon, Pune, being the parties to this Deed.

RECITALS

WHEREAS the vendor, Eric Siq, is the owner of Survey Number 5464489, Sus Gaon, Pirangut,
Pune, area 5000 sq.m. Agreed to sell to Abhav Bhanot for Rs.10,00,000. Advance Rs.1,00,000
paid. Balance via RTGS/NEFT. Execution 23 March 2026. Registration at Baner Registrar Office.
[Registration Act, 1908] Section 17(1)(b), Parliament of India, 1908

OPERATIVE CLAUSE 1

The vendor sells to the vendee Survey Number 5464489, Sus Gaon, Pirangut, Pune, 5000 sq.m.,
for Rs.10,00,000. Advance Rs.1,00,000 paid via RTGS/NEFT on 23 March 2026.
[Registration Act, 1908] Section 17(1)(b), Parliament of India, 1908

OPERATIVE CLAUSE 2

The vendor declares the property is free from encumbrances and has absolute right to sell.
[Transfer of Property Act, 1882] Section 54, Parliament of India, 1882

SCHEDULE OF PROPERTY

Survey Number 5464489, Sus Gaon, Pirangut, Pune, Maharashtra. Area 5000 sq.m. open plot.
North: Shri X. South: Shri Y. East: Shri Z. West: Shri W. Gram Panchayat Sus Gaon.

ATTESTATION

Executed 23 March 2026, Baner Registrar Office, Pune.
ERIC SIQ (Seller). ABHAV BHANOT (Buyer). Witnesses: Asho Shibu, Ameya Tipnis.
"""

# ---------------------------------------------------------------------------
# Cache context — representative statute excerpts (simulates loaded cache)
# ---------------------------------------------------------------------------

CACHE_ENTRIES = [
    _MockCacheEntry(
        name="Transfer of Property Act, 1882",
        content=(
            "Section 54 of the Transfer of Property Act, 1882 defines 'sale' as a transfer "
            "of ownership in exchange for a price paid or promised or part-paid and part-promised. "
            "Sale of immovable property of value Rs.100 or more can only be made by a registered "
            "instrument. The seller is bound to disclose any material defect in the property. "
            "The buyer is bound to disclose any fact as to the nature or extent of the seller's "
            "interest which the buyer is aware of and the seller is not aware of."
        ),
    ),
    _MockCacheEntry(
        name="Registration Act, 1908",
        content=(
            "Section 17(1)(b) of the Registration Act, 1908 mandates compulsory registration "
            "of instruments of sale, mortgage, gift, exchange, or lease of immovable property "
            "where the value exceeds Rs.100. An unregistered document required to be registered "
            "shall not affect any immovable property comprised therein, nor be received as "
            "evidence of any transaction affecting such property. Registration must be done "
            "before the Sub-Registrar of Assurances having jurisdiction over the property."
        ),
    ),
    _MockCacheEntry(
        name="MLRC 1966",
        content=(
            "The Maharashtra Land Revenue Code, 1966 governs land administration in Maharashtra. "
            "Section 32 requires that any transfer of agricultural land be recorded in the "
            "Village Form 7/12 (Satbara Utara). The mutation entry must be made within 3 months "
            "of the registered deed. The Talathi is responsible for updating revenue records "
            "upon receipt of a certified copy of the registered sale deed from the Sub-Registrar."
        ),
    ),
]

# ---------------------------------------------------------------------------
# Fact pattern
# ---------------------------------------------------------------------------

FACT_PATTERN = {
    "intake": {
        "vendor_name": "Eric Siq",
        "purchaser_name": "Abhav Bhanot",
        "survey_number": "5464489",
        "village": "Sus Gaon",
        "taluka": "Pirangut",
        "district": "Pune",
        "area": "5000 square meters",
        "consideration": "10,00,000",
        "advance": "1,00,000",
        "payment_mode": "RTGS/NEFT",
        "execution_date": "23 March 2026",
        "registrar_office": "Baner Registrar Office",
        "witness_1": "Asho Shibu",
        "witness_2": "Ameya Tipnis",
    }
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("\n" + "=" * 65)
    print("  RAGAS Tier 3 Integration Test — Sale Deed")
    print("=" * 65)

    doc   = _MockDoc(body=SALE_DEED_BODY)
    cache = _MockCache(documents=CACHE_ENTRIES, total_tokens=1500)

    print("\n  LLM judge : Groq llama-3.3-70b-versatile (GROQ_RAGAS_API_KEY)")
    print("  Embeddings: all-MiniLM-L6-v2 (local, sentence-transformers)")
    print("  Metrics   : faithfulness, answer_relevance,")
    print("              context_precision, context_recall")
    print("\n  Running... (expect ~20-40s for 4 LLM-judged metrics)\n")

    from src.evaluation.evaluator import evaluate_ragas

    t0 = time.time()
    scores = evaluate_ragas(doc=doc, cache=cache, fact_pattern=FACT_PATTERN)
    elapsed = time.time() - t0

    if not scores:
        print("  ERROR: evaluate_ragas() returned empty dict.")
        print("  Check GROQ_RAGAS_API_KEY in .env and re-run.")
        sys.exit(1)

    sep = "-" * 65
    print(sep)
    print(f"  faithfulness        : {scores.get('faithfulness', 0):.4f}  "
          f"(claims grounded in cache)")
    print(f"  answer_relevance    : {scores.get('answer_relevance', 0):.4f}  "
          f"(output addresses fact pattern)")
    print(f"  context_precision   : {scores.get('context_precision', 0):.4f}  "
          f"(fraction of cache actually used)")
    print(f"  context_recall      : {scores.get('context_recall', 0):.4f}  "
          f"(cache contained what was needed)")
    print(sep)
    print(f"  Elapsed: {elapsed:.1f}s")
    print("=" * 65)

    # Interpret
    faith = scores.get("faithfulness", 0)
    if faith >= 0.8:
        verdict = "Strong — LLM claims are well-grounded in the cache"
    elif faith >= 0.5:
        verdict = "Moderate — some claims not traceable to cache context"
    else:
        verdict = "Weak — significant hallucination risk detected"
    print(f"\n  Faithfulness verdict: {verdict}")

    # Save
    out = ROOT / "output" / "ragas_test_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({
            "timestamp": datetime.now().isoformat(),
            "doc_type": "sale_deed",
            "run_id": doc.run_id,
            "elapsed_seconds": round(elapsed, 2),
            "scores": scores,
        }, indent=2),
        encoding="utf-8",
    )
    print(f"\n  Saved → {out.relative_to(ROOT)}\n")


if __name__ == "__main__":
    main()
