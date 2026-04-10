#!/usr/bin/env python3
"""
scripts/establish_quality_baselines.py

Run Tier 1 + Tier 2 evaluation on one real generated PDF per document type
and write results to output/quality_baselines.json.

Usage:
    python scripts/establish_quality_baselines.py
"""
from __future__ import annotations

import json
import re
import sys
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: PyMuPDF not installed. Run: pip install pymupdf")
    sys.exit(1)


def extract_pdf_text(pdf_path: str) -> str:
    doc = fitz.open(pdf_path)
    pages = [page.get_text() for page in doc]
    doc.close()
    return "\n".join(pages)


# ---------------------------------------------------------------------------
# CAG evaluator import
# ---------------------------------------------------------------------------

from src.evaluation.evaluator import evaluate_cag_document  # noqa: E402


@dataclass
class _MockDoc:
    """Minimal stand-in for GeneratedDocument."""
    content: str
    body: str
    citations: list = field(default_factory=list)
    ungrounded_clauses: list = field(default_factory=list)
    run_id: str = "baseline"
    doc_type: str = "unknown"
    pipeline_variant: str = "CAG"


# ---------------------------------------------------------------------------
# Citation parser (same logic as evaluate_single_pdf.py)
# ---------------------------------------------------------------------------

def _parse_citations(text: str) -> tuple[list[str], list[int]]:
    """
    Grounded = lines containing a legal act citation like [Registration Act, 1908 ...]
    Ungrounded = lines flagged [UNGROUNDED ...] or containing placeholder [Legislature/Court]
    """
    grounded, ungrounded_lines = [], []
    for i, line in enumerate(text.splitlines()):
        if re.search(r"\[UNGROUNDED", line, re.I):
            ungrounded_lines.append(i)
        elif re.search(r"\[.*(Act|Code|Section).*\]", line, re.I):
            grounded.append(line.strip())
    return grounded, ungrounded_lines


# ---------------------------------------------------------------------------
# Per-doc-type configuration
# ---------------------------------------------------------------------------

DOC_CONFIGS = {
    "sale_deed": {
        "fact_pattern": {
            "seller_name": "Ramesh Kumar",
            "buyer_name": "Suresh Patil",
            "survey_number": "123/4A",
            "village": "Sus Gaon",
            "taluka": "Haveli",
            "district": "Pune",
            "area": "500 square meters",
            "consideration": "50,00,000",
            "execution_date": "20 March 2026",
        },
        "slots": [
            "PARTIES_CLAUSE", "RECITALS", "OPERATIVE_CLAUSE_1",
            "OPERATIVE_CLAUSE_2", "OPERATIVE_CLAUSE_3",
            "SCHEDULE_OF_PROPERTY", "ATTESTATION",
        ],
    },
    "mortgage_deed": {
        "fact_pattern": {
            "mortgagor_name": "Anil Sharma",
            "mortgagee_name": "State Bank of India",
            "survey_number": "456/2B",
            "village": "Kothrud",
            "district": "Pune",
            "loan_amount": "25,00,000",
            "interest_rate": "8.5",
            "loan_period": "20 years",
        },
        "slots": [
            "PARTIES_CLAUSE", "RECITALS", "OPERATIVE_CLAUSE_1",
            "OPERATIVE_CLAUSE_2", "SCHEDULE_OF_PROPERTY", "ATTESTATION",
        ],
    },
    "gift_deed": {
        "fact_pattern": {
            "donor_name": "Vijay Desai",
            "donee_name": "Priya Desai",
            "survey_number": "789/1C",
            "village": "Wakad",
            "district": "Pune",
            "area": "200 square meters",
        },
        "slots": [
            "PARTIES_CLAUSE", "RECITALS", "OPERATIVE_CLAUSE_1",
            "OPERATIVE_CLAUSE_2", "SCHEDULE_OF_PROPERTY", "ATTESTATION",
        ],
    },
    "affidavit": {
        "fact_pattern": {
            "deponent_name": "Mohan Joshi",
            "deponent_age": "45",
            "deponent_address": "Pune, Maharashtra",
            "purpose": "property ownership declaration",
        },
        "slots": ["PARTIES_CLAUSE", "RECITALS", "OPERATIVE_CLAUSE_1", "ATTESTATION"],
    },
    "conveyance_deed": {
        "fact_pattern": {
            "conveyor_name": "ABC Developers",
            "conveyee_name": "Rajesh Mehta",
            "flat_number": "B-304",
            "building_name": "Sunrise Towers",
            "district": "Mumbai",
            "consideration": "1,20,00,000",
            "mahaRERA_number": "P52100012345",
        },
        "slots": [
            "PARTIES_CLAUSE", "RECITALS", "OPERATIVE_CLAUSE_1",
            "OPERATIVE_CLAUSE_2", "SCHEDULE_OF_PROPERTY", "ATTESTATION",
        ],
    },
    "power_of_attorney": {
        "fact_pattern": {
            "principal_name": "Sanjay Kulkarni",
            "attorney_name": "Deepak Kulkarni",
            "scope": "property management and sale",
            "district": "Pune",
        },
        "slots": [
            "PARTIES_CLAUSE", "RECITALS", "OPERATIVE_CLAUSE_1",
            "OPERATIVE_CLAUSE_2", "ATTESTATION",
        ],
    },
    "leave_and_license": {
        "fact_pattern": {
            "licensor_name": "Prakash Nair",
            "licensee_name": "Amit Verma",
            "premises_address": "Flat 201, Shivaji Nagar, Pune",
            "license_fee": "25,000",
            "deposit": "1,00,000",
            "period": "11 months",
        },
        "slots": [
            "PARTIES_CLAUSE", "RECITALS", "OPERATIVE_CLAUSE_1",
            "OPERATIVE_CLAUSE_2", "ATTESTATION",
        ],
    },
}

# Known PDF paths (hardcoded; auto-discovery used as fallback)
KNOWN_PDF_PATHS = {
    "sale_deed": "output/cag/sale_deed/7c040c68-a430-4009-b405-d92a28705728/7c040c68-a430-4009-b405-d92a28705728.pdf",
    "mortgage_deed": "output/cag/mortgage_deed/34938404-86ea-4649-8d6f-03efdcd56013/34938404-86ea-4649-8d6f-03efdcd56013.pdf",
    "gift_deed": "output/cag/gift_deed/87321624-9af6-41e5-a8c7-1ed472a67e52/87321624-9af6-41e5-a8c7-1ed472a67e52.pdf",
    "affidavit": "output/cag/affidavit/1945fbc2-4aef-44f7-bbd5-da5b6718959f/1945fbc2-4aef-44f7-bbd5-da5b6718959f.pdf",
    "conveyance_deed": "output/cag/conveyance_deed/b95a63e4-6e66-47d9-9ddf-5b35d557d381/b95a63e4-6e66-47d9-9ddf-5b35d557d381.pdf",
    "power_of_attorney": "output/cag/power_of_attorney/804f900b-15bb-4a7e-bda0-96721a123b1b/804f900b-15bb-4a7e-bda0-96721a123b1b.pdf",
    "leave_and_license": "output/cag/leave_and_license/6d6bcf4b-42db-457c-8819-d56b13942a73/6d6bcf4b-42db-457c-8819-d56b13942a73.pdf",
}

# Thresholds
THRESHOLDS = {
    "cache_hit_rate": 0.75,
    "fact_fidelity_score": 0.85,
    "section_completeness": 1.0,
    "citation_format_compliance": 0.80,
    "jurisdictional_accuracy": 0.90,
    "ragas_faithfulness": 0.60,
}


# ---------------------------------------------------------------------------
# PDF discovery
# ---------------------------------------------------------------------------

def find_pdf(doc_type: str) -> str | None:
    """Return the PDF path for a doc type: try known path first, then auto-discover."""
    known = ROOT / KNOWN_PDF_PATHS.get(doc_type, "")
    if known.exists():
        return str(known)

    # Auto-discover: scan output/cag/{doc_type}/*/  for any .pdf
    cag_dir = ROOT / "output" / "cag" / doc_type
    if cag_dir.exists():
        pdfs = sorted(cag_dir.glob("*/*.pdf"))
        if pdfs:
            return str(pdfs[-1])  # most recent by sort order

    return None


# ---------------------------------------------------------------------------
# Evaluate one document type
# ---------------------------------------------------------------------------

def evaluate_doc_type(doc_type: str) -> dict:
    pdf_path = find_pdf(doc_type)
    if pdf_path is None:
        return {
            "doc_type": doc_type,
            "pdf_path": None,
            "error": "PDF not found",
            "issues": ["PDF not found — cannot evaluate"],
        }

    print(f"  [{doc_type}] Extracting text from: {pdf_path}")
    text = extract_pdf_text(pdf_path)
    if not text.strip():
        return {
            "doc_type": doc_type,
            "pdf_path": pdf_path,
            "error": "Empty PDF text",
            "issues": ["Could not extract text from PDF"],
        }

    cfg = DOC_CONFIGS[doc_type]
    grounded_citations, ungrounded_lines = _parse_citations(text)

    mock_doc = _MockDoc(
        content=text,
        body=text,
        citations=grounded_citations,
        ungrounded_clauses=ungrounded_lines,
        run_id=Path(pdf_path).stem,
        doc_type=doc_type,
        pipeline_variant="CAG",
    )

    result = evaluate_cag_document(
        doc=mock_doc,
        fact_pattern=cfg["fact_pattern"],
        template_slots=cfg["slots"],
        latency_seconds=0.0,
    )

    summary = result.summary()

    # Try RAGAS (skip gracefully if unavailable / no API key)
    ragas_faithfulness = None
    try:
        from src.evaluation.evaluator import evaluate_ragas

        class _FakeCache:
            documents = []

        ragas_scores = evaluate_ragas(mock_doc, _FakeCache(), cfg["fact_pattern"])
        if ragas_scores:
            ragas_faithfulness = ragas_scores.get("faithfulness")
            summary["ragas_faithfulness"] = ragas_faithfulness
    except Exception:
        pass  # RAGAS unavailable — leave as null

    # Identify issues
    issues = []
    if summary.get("cache_hit_rate", 1.0) < THRESHOLDS["cache_hit_rate"]:
        issues.append(f"cache_hit_rate {summary['cache_hit_rate']:.4f} below threshold {THRESHOLDS['cache_hit_rate']} → cache manifest gap")
    if summary.get("fact_fidelity_score", 1.0) < THRESHOLDS["fact_fidelity_score"]:
        issues.append(f"fact_fidelity_score {summary['fact_fidelity_score']:.4f} below threshold {THRESHOLDS['fact_fidelity_score']} → prompt not injecting intake fields correctly")
    if summary.get("section_completeness", 1.0) < THRESHOLDS["section_completeness"]:
        issues.append(f"section_completeness {summary['section_completeness']:.4f} below threshold {THRESHOLDS['section_completeness']} → slot not being filled by two-pass generation")
    if summary.get("citation_format_compliance", 1.0) < THRESHOLDS["citation_format_compliance"]:
        issues.append(f"citation_format_compliance {summary['citation_format_compliance']:.4f} below threshold {THRESHOLDS['citation_format_compliance']} → placeholder tokens surviving post-processing")
    if summary.get("jurisdictional_accuracy", 1.0) < THRESHOLDS["jurisdictional_accuracy"]:
        issues.append(f"jurisdictional_accuracy {summary['jurisdictional_accuracy']:.4f} below threshold {THRESHOLDS['jurisdictional_accuracy']} → LLM citing non-Maharashtra statutes")
    if ragas_faithfulness is not None and ragas_faithfulness < THRESHOLDS["ragas_faithfulness"]:
        issues.append(f"ragas_faithfulness {ragas_faithfulness:.4f} below threshold {THRESHOLDS['ragas_faithfulness']} → claims not grounded in cache context")

    return {
        **summary,
        "pdf_path": pdf_path,
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------

METRIC_COLS = [
    ("cache_hit_rate",            "CacheHit"),
    ("fact_fidelity_score",       "FactFid"),
    ("section_completeness",      "SecComp"),
    ("citation_format_compliance","CitFmt"),
    ("jurisdictional_accuracy",   "JurisAcc"),
    ("ragas_faithfulness",        "RAGAS-F"),
]


def _fmt(val, threshold: float) -> str:
    if val is None:
        return "  N/A  "
    mark = "✓" if val >= threshold else "❌"
    return f"{val:.3f}{mark}"


def print_summary_table(baselines: dict) -> None:
    sep = "=" * 100
    print()
    print(sep)
    print("  QUALITY BASELINES SUMMARY")
    print(sep)

    # Header
    col_w = 10
    header = f"  {'Doc Type':<25s}"
    for _, label in METRIC_COLS:
        header += f"  {label:>{col_w}}"
    print(header)
    print("  " + "-" * 97)

    for doc_type, data in baselines.items():
        if "error" in data:
            print(f"  {doc_type:<25s}  ERROR: {data['error']}")
            continue
        row = f"  {doc_type:<25s}"
        for metric, _ in METRIC_COLS:
            val = data.get(metric)
            threshold = THRESHOLDS.get(metric, 1.0)
            row += f"  {_fmt(val, threshold):>{col_w}}"
        print(row)

    print()
    print("  Thresholds:")
    for metric, label in METRIC_COLS:
        print(f"    {label:<12s} ≥ {THRESHOLDS.get(metric, 1.0)}")

    print()
    print("  Issues found:")
    any_issues = False
    for doc_type, data in baselines.items():
        issues = data.get("issues", [])
        if issues:
            any_issues = True
            print(f"  [{doc_type}]")
            for issue in issues:
                print(f"    ❌ {issue}")
    if not any_issues:
        print("  ✓ All document types pass all thresholds.")
    print(sep)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 70)
    print("  Establishing Per-Document-Type Quality Baselines")
    print("=" * 70)

    baselines: dict = {}
    issues_summary: dict = {}

    for doc_type in DOC_CONFIGS:
        result = evaluate_doc_type(doc_type)
        baselines[doc_type] = result
        issues_summary[doc_type] = result.get("issues", [])

    # Write JSON
    out_path = ROOT / "output" / "quality_baselines.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    output = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "thresholds": THRESHOLDS,
        "baselines": baselines,
        "issues_summary": issues_summary,
    }

    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nJSON saved → {out_path.relative_to(ROOT)}")

    print_summary_table(baselines)


if __name__ == "__main__":
    main()
