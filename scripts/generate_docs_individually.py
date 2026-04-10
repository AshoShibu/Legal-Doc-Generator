#!/usr/bin/env python3
"""
scripts/generate_docs_individually.py

Generates 5 legal documents as completely separate, independent Groq API requests.
Each document is self-contained — no shared pipeline state between them.

Documents:
  1. Mortgage Deed
  2. Leave and License Agreement
  3. Gift Deed
  4. Conveyance Deed
  5. Affidavit

Usage:
    python scripts/generate_docs_individually.py
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import requests as _requests

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL   = "llama-3.1-8b-instant"
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"
OUTPUT_ROOT  = ROOT / "output" / "individual"

# ---------------------------------------------------------------------------
# Document definitions — each is fully self-contained
# ---------------------------------------------------------------------------

DOCUMENTS = [
    {
        "id": "mortgage_deed",
        "title": "Simple Mortgage Deed",
        "parties": {
            "Mortgagor": "Suresh Narayan Patil, House No. 7, Ganesh Peth, Solapur – 413 001, Maharashtra",
            "Mortgagee": "Bank of Maharashtra, Main Branch, Vijapur Road, Solapur – 413 003, Maharashtra",
        },
        "facts": {
            "Loan Amount": "₹40,00,000 (Rupees Forty Lakhs only)",
            "Interest Rate": "8.75% per annum (floating, linked to MCLR)",
            "Repayment Period": "180 months (15 years)",
            "EMI": "₹39,860 per month",
            "Mortgage Type": "Simple Mortgage — possession remains with Mortgagor",
            "Property": "Survey No. 112/3B, Gat No. 445, Village Nandal, Taluka Solapur North, District Solapur, Maharashtra — 810 sq. metres agricultural land",
            "Execution Date": "10 March 2026",
            "Witnesses": "Vijay Ramchandra Shinde; Meena Ashok Desai",
        },
        "statutes": [
            "Transfer of Property Act 1882, Section 58 — defines Simple Mortgage",
            "Registration Act 1908, Section 17 — compulsory registration of mortgage deeds",
            "Maharashtra Land Revenue Code 1966, Section 32 — mutation entry in Village Form 7/12",
        ],
    },
    {
        "id": "leave_and_license",
        "title": "Leave and License Agreement",
        "parties": {
            "Licensor": "Anita Rajendra Mehta, Flat No. 602, Oberoi Gardens, Kandivali East, Mumbai – 400 101",
            "Licensee": "Rohit Ashish Kapoor, C/o Kapoor Enterprises, 14 Nariman Point, Mumbai – 400 021",
        },
        "facts": {
            "Premises": "Flat No. 401, 4th Floor, Oberoi Gardens, Thakur Village, Kandivali East, Mumbai – 400 101 (950 sq. ft. carpet area, CTS No. 2/A of Village Kandivali, Mumbai Suburban)",
            "Monthly License Fee": "₹42,000 per month (payable by 5th of each month)",
            "Security Deposit": "₹2,52,000 (6 months — refundable within 30 days of vacation)",
            "Duration": "11 months commencing 01 April 2026",
            "Lock-in Period": "6 months",
            "Notice Period": "30 days",
            "Permitted Use": "Residential only — subletting not permitted",
            "Maintenance": "Licensor: structural; Licensee: day-to-day",
            "Witnesses": "Harish Dilip Shah; Kavita Nitin Jain",
        },
        "statutes": [
            "Maharashtra Rent Control Act 1999, Section 24 — leave and license agreements",
            "Registration Act 1908, Section 17 — compulsory registration of L&L agreements",
            "Maharashtra Land Revenue Code 1966, Section 32 — revenue records",
        ],
    },
    {
        "id": "gift_deed",
        "title": "Gift Deed",
        "parties": {
            "Donor": "Vasudha Krishnarao Iyer, Bungalow No. 5, Saraswati Colony, Nashik Road, Nashik – 422 101",
            "Donee": "Arjun Vasudha Iyer (Son of Donor), Flat No. 203, Lotus Heights, College Road, Nashik – 422 005",
        },
        "facts": {
            "Relationship": "Mother gifting to Son — out of natural love and affection",
            "Property": "Survey No. 34/2, Gat No. 210, Village Nashik Road, Taluka Nashik, District Nashik — 405 sq. metres",
            "Nature of Gift": "Unconditional — no monetary consideration",
            "Donee Acceptance": "Yes — Donee has accepted the gift",
            "Possession Delivery": "15 March 2026",
            "Execution Date": "15 March 2026",
            "Witnesses": "Suresh Balaji Kulkarni; Lata Mohan Deshpande",
        },
        "statutes": [
            "Transfer of Property Act 1882, Section 122 — definition of gift",
            "Transfer of Property Act 1882, Section 123 — registration of gift deed for immovable property",
            "Registration Act 1908, Section 17 — compulsory registration",
            "Maharashtra Land Revenue Code 1966, Section 32 — mutation entry in Village Form 7/12",
        ],
    },
    {
        "id": "conveyance_deed",
        "title": "Conveyance Deed",
        "parties": {
            "Developer": "Godrej Properties Limited, Godrej One, Pirojshanagar, Eastern Express Highway, Vikhroli East, Mumbai – 400 079 (MahaRERA No. P51700025431)",
            "Purchaser": "Nikhil Sanjay Wagh, Flat No. 1204, Tower B, Godrej Emerald, Thane West – 400 610",
            "Society": "Godrej Emerald Co-operative Housing Society Ltd.",
        },
        "facts": {
            "Property": "Flat No. 1204, 12th Floor, Tower B, Godrej Emerald — Carpet Area: 872 sq. ft., Built-up: 1,105 sq. ft., Common Area Share: 233 sq. ft.",
            "Land": "CTS No. 45/A, Village Majiwada, District Thane, Maharashtra",
            "Consideration": "₹1,25,00,000 (Rupees One Crore Twenty-Five Lakhs only)",
            "Stamp Duty Paid": "₹7,50,000",
            "Occupancy Certificate": "Received 12 December 2025",
            "MahaRERA Registration": "P51700025431",
            "Execution Date": "18 March 2026",
            "Witnesses": "Pradeep Ramesh Tiwari; Smita Arun Kulkarni",
        },
        "statutes": [
            "Transfer of Property Act 1882, Section 54 — sale and conveyance of immovable property",
            "Registration Act 1908, Section 17 — compulsory registration",
            "Real Estate (Regulation and Development) Act 2016 — MahaRERA compliance",
            "Maharashtra Co-operative Societies Act 1960 — society membership obligations",
            "Maharashtra Land Revenue Code 1966, Section 32 — mutation entry",
        ],
    },
    {
        "id": "affidavit",
        "title": "Affidavit",
        "parties": {
            "Deponent": "Mangesh Dattatray Sawant, aged 42, Government Employee (Maharashtra State Electricity Board), House No. 14, Shivaji Chowk, Ratnagiri – 415 612",
        },
        "facts": {
            "Purpose": "Property Ownership — submitted to Sub-Registrar, Ratnagiri",
            "Statement 1": "I am the absolute and lawful owner of agricultural land bearing Survey No. 67/1, admeasuring 1.20 Hectares, Village Pawas, Taluka Ratnagiri, District Ratnagiri, Maharashtra.",
            "Statement 2": "The land was inherited from my late father Shri Dattatray Vishnu Sawant (deceased 05 June 2018); mutation entry No. 1245 is recorded in Village Form 7/12 in my name.",
            "Statement 3": "The property is free from all encumbrances, mortgages, charges, liens, attachments, and claims of any nature.",
            "Statement 4": "No sale deed, gift deed, or instrument of transfer has been executed by me or my predecessors except the present transaction.",
            "Statement 5": "This affidavit is made for registration of the Sale Deed in favour of the purchaser and to satisfy the Sub-Registrar as to my title.",
            "Notarisation": "Notary Public, Ratnagiri",
            "Execution Date": "12 March 2026",
        },
        "statutes": [
            "Indian Evidence Act 1872, Section 3 — admissibility of affidavits",
            "Registration Act 1908, Section 18 — optional registration of affidavits",
            "Maharashtra Land Revenue Code 1966, Section 32 — revenue record update",
        ],
    },
]


# ---------------------------------------------------------------------------
# Groq API call
# ---------------------------------------------------------------------------

def call_groq(prompt: str, doc_title: str) -> str:
    """Make a single independent Groq API request for one document."""
    if not GROQ_API_KEY:
        return "[ERROR] GROQ_API_KEY not set in .env"

    try:
        resp = _requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 2048,
            },
            timeout=(10, 60),
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        return f"[GROQ ERROR] {exc}"


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_prompt(doc: dict) -> str:
    parties_block = "\n".join(f"  {role}: {detail}" for role, detail in doc["parties"].items())
    facts_block   = "\n".join(f"  {k}: {v}" for k, v in doc["facts"].items())
    statutes_block = "\n".join(f"  - {s}" for s in doc["statutes"])

    return f"""You are a Maharashtra legal document drafting assistant.

Draft a complete, realistic {doc['title']} for use in Maharashtra, India.

PARTIES:
{parties_block}

FACTS (use these exact values — do not substitute or invent):
{facts_block}

APPLICABLE STATUTES (cite these inline in each operative clause):
{statutes_block}

INSTRUCTIONS:
1. Structure: Parties → Recitals → Operative Clauses (numbered) → Schedule of Property → Attestation
2. Cite each statute inline as: [Act Name, Year] Section X — [brief description]
3. Use exact names, amounts, dates, and survey numbers from the FACTS above
4. Apply Maharashtra jurisdiction throughout
5. Be complete and professional — this is a real legal document draft
6. End with signature blocks for all parties and two witnesses

Draft the {doc['title']} now:
"""


# ---------------------------------------------------------------------------
# Save output
# ---------------------------------------------------------------------------

def save_output(doc_id: str, content: str, run_id: str) -> Path:
    out_dir = OUTPUT_ROOT / doc_id / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{doc_id}.txt"
    out_file.write_text(content, encoding="utf-8")

    # Also save as DOCX via the generation pipeline
    try:
        sys.path.insert(0, str(ROOT))
        from src.generation.document_generator import generate_document
        from src.generation.exporter import export_document
        from src.cag.engine import LegalCache, CacheEntry

        dummy_cache = LegalCache(
            documents=[CacheEntry(name="Groq Direct", path="", tokens=0, content="")],
            total_tokens=0,
            llm_backend="groq_llama3_8b",
            session_id=run_id,
            omitted_documents=[],
            document_type=doc_id,
        )
        generated = generate_document(
            llm_output=content,
            template=None,
            cache_or_chunks=dummy_cache,
            doc_type=doc_id,
            pipeline_variant="Groq-Direct",
            llm_model="groq_llama3_8b",
            run_id=run_id,
            cache_version="groq-direct",
        )
        export_document(generated, str(out_dir), run_id)
    except Exception as exc:
        print(f"    (DOCX/PDF export skipped: {exc})")

    return out_file


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 68)
    print("  Maharashtra Legal Documents — Individual Groq Requests")
    print("=" * 68)
    print(f"  Model  : {GROQ_MODEL}")
    print(f"  Output : {OUTPUT_ROOT}")
    print(f"  Docs   : {len(DOCUMENTS)}")
    print("=" * 68)

    if not GROQ_API_KEY:
        print("\nERROR: GROQ_API_KEY not set. Add it to .env and retry.")
        sys.exit(1)

    results = []
    for i, doc in enumerate(DOCUMENTS, 1):
        run_id = str(uuid.uuid4())
        print(f"\n[{i}/{len(DOCUMENTS)}] {doc['title']}")
        print(f"  Run ID : {run_id[:8]}...")
        print(f"  Sending independent Groq request...", end=" ", flush=True)

        import time
        t0 = time.time()
        prompt  = build_prompt(doc)
        content = call_groq(prompt, doc["title"])
        elapsed = time.time() - t0

        if content.startswith("[GROQ ERROR]") or content.startswith("[ERROR]"):
            print(f"FAILED  ({elapsed:.1f}s)")
            print(f"  Error: {content}")
            results.append({"doc": doc["title"], "status": "FAILED", "elapsed": elapsed})
            continue

        out_file = save_output(doc["id"], content, run_id)
        print(f"OK  ({elapsed:.1f}s)")
        print(f"  Saved : {out_file.relative_to(ROOT)}")

        preview = "\n".join(content.strip().splitlines()[:3])
        print(f"  Preview: {preview[:120]}...")

        results.append({"doc": doc["title"], "status": "OK", "elapsed": elapsed, "path": str(out_file)})

    # Summary
    print("\n" + "=" * 68)
    print("  SUMMARY")
    print("=" * 68)
    for r in results:
        print(f"  {r['status']:<8}  {r['doc']:<35} ({r['elapsed']:.1f}s)")
    passed = sum(1 for r in results if r["status"] == "OK")
    print(f"\n  {passed}/{len(DOCUMENTS)} documents generated successfully")
    print(f"  Output: {OUTPUT_ROOT}/")
    print()


if __name__ == "__main__":
    main()
