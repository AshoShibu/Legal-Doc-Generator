#!/usr/bin/env python3
"""
scripts/review_clause_quality.py

Clause-by-clause automated quality review for all 7 Maharashtra legal document types.

For each fact pattern in config/eval_queries.json:
  1. Generates a document via the CAG engine
  2. Runs checklist checks (string-based, no LLM needed)
  3. Writes failures to output/clause_review_failures.json
  4. Writes per-document pass/fail summary to output/clause_review_report.json
  5. Prints a human-readable summary table

Usage:
    python scripts/review_clause_quality.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Template slots per document type (mirrors establish_quality_baselines.py)
# ---------------------------------------------------------------------------

TEMPLATE_SLOTS: dict[str, list[str]] = {
    "sale_deed": [
        "PARTIES_CLAUSE", "RECITALS",
        "OPERATIVE_CLAUSE_1", "OPERATIVE_CLAUSE_2", "OPERATIVE_CLAUSE_3",
        "SCHEDULE", "ATTESTATION",
    ],
    "mortgage_deed": [
        "PARTIES_CLAUSE", "RECITALS",
        "OPERATIVE_CLAUSE_1", "OPERATIVE_CLAUSE_2",
        "SCHEDULE", "ATTESTATION",
    ],
    "power_of_attorney": [
        "PARTIES_CLAUSE", "RECITALS",
        "OPERATIVE_CLAUSE_1", "OPERATIVE_CLAUSE_2",
        "ATTESTATION",
    ],
    "leave_and_license": [
        "PARTIES_CLAUSE", "RECITALS",
        "OPERATIVE_CLAUSE_1", "OPERATIVE_CLAUSE_2",
        "ATTESTATION",
    ],
    "gift_deed": [
        "PARTIES_CLAUSE", "RECITALS",
        "OPERATIVE_CLAUSE_1", "OPERATIVE_CLAUSE_2",
        "SCHEDULE", "ATTESTATION",
    ],
    "conveyance_deed": [
        "PARTIES_CLAUSE", "RECITALS",
        "OPERATIVE_CLAUSE_1", "OPERATIVE_CLAUSE_2",
        "SCHEDULE", "ATTESTATION",
    ],
    "affidavit": [
        "PARTIES_CLAUSE", "RECITALS",
        "OPERATIVE_CLAUSE_1",
        "ATTESTATION",
    ],
}

# ---------------------------------------------------------------------------
# Checklist helpers
# ---------------------------------------------------------------------------

_DISTRICT_NAMES = [
    "Pune", "Mumbai", "Thane", "Nashik", "Nagpur", "Aurangabad",
    "Solapur", "Kolhapur", "Satara", "Sangli", "Nanded", "Latur",
    "Ahmednagar", "Jalgaon", "Akola", "Amravati", "Chandrapur",
    "Dhule", "Gondia", "Hingoli", "Jalna", "Nandurbar", "Osmanabad",
    "Palghar", "Parbhani", "Raigad", "Ratnagiri", "Sindhudurg",
    "Wardha", "Washim", "Yavatmal",
]

_PLACEHOLDER_PATTERNS = ["<PETITIONER_", "<RESPONDENT_", "<OWNER_"]


def _contains_any(text: str, keywords: list[str], case_sensitive: bool = False) -> bool:
    if not case_sensitive:
        text_lower = text.lower()
        return any(k.lower() in text_lower for k in keywords)
    return any(k in text for k in keywords)


def _contains_placeholder(text: str) -> bool:
    return any(p in text for p in _PLACEHOLDER_PATTERNS)


# ---------------------------------------------------------------------------
# Per-section checklist functions
# ---------------------------------------------------------------------------

def check_parties(text: str, doc_type: str, intake: dict) -> dict[str, Any]:
    """Check PARTIES clause."""
    failures: list[str] = []

    # Role labels by doc type
    role_map = {
        "sale_deed":         ["Vendor", "Purchaser"],
        "mortgage_deed":     ["Mortgagor", "Mortgagee"],
        "power_of_attorney": ["Principal", "Attorney"],
        "leave_and_license": ["Licensor", "Licensee"],
        "gift_deed":         ["Donor", "Donee"],
        "conveyance_deed":   ["Developer", "Purchaser"],
        "affidavit":         ["Deponent"],
    }
    required_roles = role_map.get(doc_type, [])
    for role in required_roles:
        if role.lower() not in text.lower():
            failures.append(f"Missing role label: '{role}'")

    # Full address check — at least one district/city name
    if not _contains_any(text, _DISTRICT_NAMES):
        failures.append("No district/city name found in PARTIES clause (address check failed)")

    # No placeholder tokens
    if _contains_placeholder(text):
        found = [p for p in _PLACEHOLDER_PATTERNS if p in text]
        failures.append(f"Placeholder tokens visible: {found}")

    return {"passed": len(failures) == 0, "failures": failures}


def check_recitals(text: str, doc_type: str, intake: dict) -> dict[str, Any]:
    """Check RECITALS clause."""
    failures: list[str] = []

    # Ownership history
    if not _contains_any(text, ["Survey", "Gat", "property", "owned", "ownership"]):
        failures.append("Ownership history not present (no 'Survey', 'Gat', 'property', or 'owned')")

    # Purpose keyword by doc type
    purpose_map = {
        "sale_deed":         ["sale", "transfer", "convey"],
        "mortgage_deed":     ["mortgage", "loan", "security"],
        "power_of_attorney": ["attorney", "authoris", "power", "appoint"],
        "leave_and_license": ["license", "licens", "occupy", "premises"],
        "gift_deed":         ["gift", "donate", "love", "affection"],
        "conveyance_deed":   ["convey", "transfer", "developer", "society"],
        "affidavit":         ["deponent", "affirm", "state", "declare", "sworn"],
    }
    purpose_keywords = purpose_map.get(doc_type, [])
    if purpose_keywords and not _contains_any(text, purpose_keywords):
        failures.append(
            f"Purpose of deed not stated (expected one of: {purpose_keywords})"
        )

    # No generic "Party A / Party B" language
    if _contains_any(text, ["Party A", "Party B", "Party C"]):
        failures.append("Generic 'Party A / Party B' language found in RECITALS")

    return {"passed": len(failures) == 0, "failures": failures}


def check_operative(text: str, doc_type: str, intake: dict) -> dict[str, Any]:
    """Check OPERATIVE CLAUSES (combined text of all operative slots)."""
    failures: list[str] = []

    # Consideration amount for property deeds
    property_deeds_with_consideration = {
        "sale_deed", "mortgage_deed", "conveyance_deed", "gift_deed",
    }
    if doc_type in property_deeds_with_consideration:
        # Check for numeric amount or currency symbol
        if not _contains_any(text, ["₹", "Rs.", "Rupees", "consideration", "amount"]):
            failures.append("Consideration amount not present")

    # Payment mode for sale_deed and conveyance_deed
    if doc_type in ("sale_deed", "conveyance_deed"):
        payment_keywords = ["RTGS", "NEFT", "cheque", "demand draft", "cash", "payment mode", "mode of payment"]
        if not _contains_any(text, payment_keywords):
            failures.append("Payment mode not stated")

    # Encumbrance declaration for sale_deed
    if doc_type == "sale_deed":
        if not _contains_any(text, ["encumbrance", "free from", "charge", "lien", "mortgage"]):
            failures.append("Encumbrance declaration not present")

    # Scope of authority for power_of_attorney
    if doc_type == "power_of_attorney":
        if not _contains_any(text, ["authorised", "empowered", "authority", "authoriz"]):
            failures.append("Scope of authority not present (no 'authorised' or 'empowered')")

    # Loan terms for mortgage_deed
    if doc_type == "mortgage_deed":
        if not _contains_any(text, ["interest", "rate", "%", "per annum", "p.a."]):
            failures.append("Interest rate not present in mortgage operative clause")
        if not _contains_any(text, ["repayment", "period", "month", "year", "EMI", "instalment"]):
            failures.append("Repayment period not present in mortgage operative clause")

    return {"passed": len(failures) == 0, "failures": failures}


def check_schedule(text: str, doc_type: str, intake: dict) -> dict[str, Any]:
    """Check SCHEDULE OF PROPERTY."""
    failures: list[str] = []

    # Survey/Gat number from fact pattern
    survey_number = intake.get("survey_number", "")
    if survey_number:
        # Check for the survey number or a significant part of it
        survey_core = survey_number.replace("Gat No. ", "").replace("CTS ", "").strip()
        if survey_core and survey_core not in text and survey_number not in text:
            failures.append(
                f"Survey/Gat number '{survey_number}' not found in SCHEDULE"
            )

    # Area with unit
    if not _contains_any(text, ["sq", "hectare", "acre", "sqft", "sq.ft", "sq.m"]):
        failures.append("Area with unit not present (no 'sq', 'hectare', or 'acre')")

    # Four-direction boundaries
    for direction in ["North", "South", "East", "West"]:
        if direction.lower() not in text.lower():
            failures.append(f"Missing boundary direction: '{direction}'")

    # Village, taluka, district
    village = intake.get("village", "")
    taluka = intake.get("taluka", "")
    district = intake.get("district", "")

    if village and village.lower() not in text.lower():
        failures.append(f"Village '{village}' not found in SCHEDULE")
    if taluka and taluka.lower() not in text.lower():
        failures.append(f"Taluka '{taluka}' not found in SCHEDULE")
    if district and district.lower() not in text.lower():
        failures.append(f"District '{district}' not found in SCHEDULE")

    return {"passed": len(failures) == 0, "failures": failures}


def check_attestation(text: str, doc_type: str, intake: dict) -> dict[str, Any]:
    """Check ATTESTATION clause."""
    failures: list[str] = []

    # Execution date — check for year at minimum
    execution_date = intake.get("execution_date", intake.get("effective_date", ""))
    if execution_date:
        year = execution_date[:4]  # "2026"
        if year not in text:
            failures.append(f"Execution year '{year}' not found in ATTESTATION")

    # Sub-Registrar office for deeds requiring registration
    deeds_requiring_registration = {
        "sale_deed", "mortgage_deed", "gift_deed", "conveyance_deed",
    }
    if doc_type in deeds_requiring_registration:
        if not _contains_any(text, ["Sub-Registrar", "Sub Registrar", "Registrar", "registration"]):
            failures.append("Sub-Registrar office not named in ATTESTATION")

    # Witness names — at least 2 witnesses
    witness_1 = intake.get("witness_1_name", "")
    witness_2 = intake.get("witness_2_name", "")
    witnesses_found = 0
    if witness_1:
        # Check for last name at minimum (more robust than full name)
        last_name_1 = witness_1.split()[-1] if witness_1 else ""
        if last_name_1 and last_name_1.lower() in text.lower():
            witnesses_found += 1
        elif witness_1.lower() in text.lower():
            witnesses_found += 1
    if witness_2:
        last_name_2 = witness_2.split()[-1] if witness_2 else ""
        if last_name_2 and last_name_2.lower() in text.lower():
            witnesses_found += 1
        elif witness_2.lower() in text.lower():
            witnesses_found += 1

    # Also accept generic "witness" keyword as fallback
    if witnesses_found < 2:
        if _contains_any(text, ["Witness", "witness", "WITNESS"]):
            witnesses_found = max(witnesses_found, 1)
        if witnesses_found < 2:
            failures.append(
                f"Witness names not sufficiently present (found {witnesses_found}/2 witnesses)"
            )

    # Signature lines
    if not _contains_any(text, ["Signature", "Signed", "IN WITNESS", "WHEREOF", "sign"]):
        failures.append("Signature lines not present (no 'Signature', 'Signed', or 'IN WITNESS')")

    return {"passed": len(failures) == 0, "failures": failures}


# ---------------------------------------------------------------------------
# Document section extractor
# ---------------------------------------------------------------------------

def _extract_section(full_text: str, section_heading: str) -> str:
    """
    Extract the text of a named section from the assembled document.
    Returns the text between this heading and the next heading (or end of doc).
    """
    lines = full_text.splitlines()
    in_section = False
    section_lines: list[str] = []

    # Headings we recognise
    all_headings = [
        "PARTIES", "RECITALS", "OPERATIVE CLAUSE", "SCHEDULE",
        "ATTESTATION", "SCHEDULE OF PROPERTY",
    ]

    for line in lines:
        stripped = line.strip().upper()

        # Check if this line IS the target heading
        if section_heading.upper() in stripped:
            in_section = True
            section_lines.append(line)
            continue

        if in_section:
            # Check if we've hit the next heading
            is_next_heading = any(
                h in stripped and h != section_heading.upper()
                for h in all_headings
                if h != section_heading.upper()
            )
            if is_next_heading and stripped != section_heading.upper():
                break
            section_lines.append(line)

    return "\n".join(section_lines)


def _extract_operative_sections(full_text: str) -> str:
    """Extract all OPERATIVE CLAUSE sections combined."""
    lines = full_text.splitlines()
    in_operative = False
    operative_lines: list[str] = []

    non_operative_headings = ["PARTIES", "RECITALS", "SCHEDULE", "ATTESTATION"]

    for line in lines:
        stripped = line.strip().upper()

        if "OPERATIVE CLAUSE" in stripped:
            in_operative = True
            operative_lines.append(line)
            continue

        if in_operative:
            is_other_heading = any(h in stripped for h in non_operative_headings)
            if is_other_heading:
                in_operative = False
            else:
                operative_lines.append(line)

    return "\n".join(operative_lines)


# ---------------------------------------------------------------------------
# Run checklist on a generated document
# ---------------------------------------------------------------------------

def run_checklist(full_text: str, doc_type: str, intake: dict) -> dict[str, Any]:
    """
    Run all checklist checks on a generated document.
    Returns a dict with per-section results.
    """
    # Extract sections
    parties_text = _extract_section(full_text, "PARTIES")
    recitals_text = _extract_section(full_text, "RECITALS")
    operative_text = _extract_operative_sections(full_text)
    schedule_text = _extract_section(full_text, "SCHEDULE")
    attestation_text = _extract_section(full_text, "ATTESTATION")

    # If section extraction yields too little, fall back to full text for that check
    # (handles cases where headings differ slightly)
    if len(parties_text.strip()) < 50:
        parties_text = full_text
    if len(recitals_text.strip()) < 50:
        recitals_text = full_text
    if len(operative_text.strip()) < 50:
        operative_text = full_text
    if len(attestation_text.strip()) < 50:
        attestation_text = full_text

    checks: dict[str, Any] = {}

    checks["PARTIES"] = check_parties(parties_text, doc_type, intake)
    checks["RECITALS"] = check_recitals(recitals_text, doc_type, intake)
    checks["OPERATIVE_CLAUSES"] = check_operative(operative_text, doc_type, intake)

    # SCHEDULE only for property deeds
    if doc_type in ("sale_deed", "mortgage_deed", "gift_deed", "conveyance_deed"):
        if len(schedule_text.strip()) < 50:
            schedule_text = full_text
        checks["SCHEDULE"] = check_schedule(schedule_text, doc_type, intake)
    else:
        checks["SCHEDULE"] = {"passed": True, "failures": [], "note": "Not applicable for this doc type"}

    checks["ATTESTATION"] = check_attestation(attestation_text, doc_type, intake)

    overall_passed = all(v["passed"] for v in checks.values())
    return {"passed": overall_passed, "checks": checks}


# ---------------------------------------------------------------------------
# Document generation
# ---------------------------------------------------------------------------

def generate_document(query: dict, backend: str) -> tuple[str, str]:
    """
    Generate a document for the given query using the CAG engine.
    Returns (doc_type, generated_text).
    Raises on generation failure.
    """
    doc_type = query["doc_type"]
    intake = query["intake"]
    ocr_fields = query.get("ocr_fields", {})

    # Build fact pattern in the format expected by engine.py
    fact_pattern = {
        "document_type": doc_type,
        "intake": intake,
        "ocr_fields": ocr_fields,
    }

    from src.cag.engine import load_cache, generate_draft

    cache = load_cache(doc_type, backend)
    slots = TEMPLATE_SLOTS.get(doc_type, [])

    draft = generate_draft(
        fact_pattern=fact_pattern,
        cache=cache,
        doc_type=doc_type,
        template_slots=slots,
    )

    return doc_type, draft.content


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # Determine backend
    backend = os.environ.get("LLM_DEFAULT_BACKEND", "groq_llama3_8b")

    # Load eval queries
    queries_path = ROOT / "config" / "eval_queries.json"
    if not queries_path.exists():
        print(f"ERROR: {queries_path} not found.")
        sys.exit(1)

    with open(queries_path, encoding="utf-8") as f:
        queries: list[dict] = json.load(f)

    print("=" * 72)
    print("  Maharashtra Legal Document — Clause Quality Review")
    print(f"  Backend: {backend}")
    print(f"  Queries: {len(queries)}")
    print("=" * 72)

    output_dir = ROOT / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    all_results: list[dict] = []
    all_failures: list[dict] = []

    for i, query in enumerate(queries):
        doc_type = query["doc_type"]
        intake = query["intake"]
        print(f"\n[{i+1}/{len(queries)}] {doc_type}")

        # --- Generate ---
        generated_text = ""
        generation_status = "OK"
        try:
            _, generated_text = generate_document(query, backend)
            print(f"  Generated {len(generated_text)} chars")
        except Exception as exc:
            generation_status = f"GENERATION_FAILED: {exc}"
            print(f"  GENERATION FAILED: {exc}")

        # --- Checklist ---
        is_unavailable = (
            not generated_text
            or "UNAVAILABLE" in generated_text[:200]
            or "GENERATION_FAILED" in generation_status
        )
        if not is_unavailable:
            checklist_result = run_checklist(generated_text, doc_type, intake)
        else:
            # Mark all checks as failed if generation failed / LLM unavailable
            fail_reason = generation_status if "GENERATION_FAILED" in generation_status else "LLM_UNAVAILABLE"
            checklist_result = {
                "passed": False,
                "checks": {
                    "PARTIES": {"passed": False, "failures": [fail_reason]},
                    "RECITALS": {"passed": False, "failures": [fail_reason]},
                    "OPERATIVE_CLAUSES": {"passed": False, "failures": [fail_reason]},
                    "SCHEDULE": {"passed": False, "failures": [fail_reason]},
                    "ATTESTATION": {"passed": False, "failures": [fail_reason]},
                },
            }

        result = {
            "doc_type": doc_type,
            "passed": checklist_result["passed"],
            "generation_status": generation_status,
            "checks": checklist_result["checks"],
        }
        all_results.append(result)

        # Collect failures
        for section, section_result in checklist_result["checks"].items():
            if not section_result["passed"]:
                for failure in section_result.get("failures", []):
                    all_failures.append({
                        "doc_type": doc_type,
                        "section": section,
                        "failure": failure,
                    })

        # Print per-section status
        for section, section_result in checklist_result["checks"].items():
            status_icon = "[OK]" if section_result["passed"] else "[FAIL]"
            print(f"  {status_icon} {section}")
            for f in section_result.get("failures", []):
                print(f"      -> {f}")

        overall_icon = "[PASS]" if checklist_result["passed"] else "[FAIL]"
        print(f"  Overall: {overall_icon}")

        # Small delay between Groq calls to avoid rate limiting
        if i < len(queries) - 1 and "groq" in backend:
            time.sleep(3)

    # --- Write outputs ---
    failures_path = output_dir / "clause_review_failures.json"
    report_path = output_dir / "clause_review_report.json"

    with open(failures_path, "w", encoding="utf-8") as f:
        json.dump(all_failures, f, indent=2, ensure_ascii=False)

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 72)
    print("  SUMMARY TABLE")
    print("=" * 72)

    sections = ["PARTIES", "RECITALS", "OPERATIVE_CLAUSES", "SCHEDULE", "ATTESTATION"]
    header = f"  {'Doc Type':<25s}"
    for s in sections:
        header += f"  {s[:10]:<10s}"
    header += "  OVERALL"
    print(header)
    print("  " + "-" * 70)

    all_pass = True
    for result in all_results:
        row = f"  {result['doc_type']:<25s}"
        for s in sections:
            sec = result["checks"].get(s, {})
            icon = "OK" if sec.get("passed", False) else "FAIL"
            row += f"  {icon:<10s}"
        overall_icon = "PASS" if result["passed"] else "FAIL"
        row += f"  {overall_icon}"
        print(row)
        if not result["passed"]:
            all_pass = False

    print()
    passed_count = sum(1 for r in all_results if r["passed"])
    print(f"  Passed: {passed_count}/{len(all_results)} document types")
    print(f"  Total failures logged: {len(all_failures)}")
    print()
    print(f"  Failures -> {failures_path.relative_to(ROOT)}")
    print(f"  Report   -> {report_path.relative_to(ROOT)}")
    print("=" * 72)

    if all_failures:
        print("\n  FAILURE DETAILS:")
        for failure in all_failures:
            print(f"  [{failure['doc_type']}] {failure['section']}: {failure['failure']}")


if __name__ == "__main__":
    main()
