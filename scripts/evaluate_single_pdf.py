#!/usr/bin/env python3
"""
scripts/evaluate_single_pdf.py

Evaluate a single generated sale-deed PDF against the evaluation framework.

Usage:
    python scripts/evaluate_single_pdf.py <path_to_pdf>

Example:
    python scripts/evaluate_single_pdf.py "C:/Users/Eric Siqueira/Downloads/sale_deed_7c040c68.pdf"
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
# BLEU / ROUGE
# ---------------------------------------------------------------------------

try:
    import nltk
    from nltk.translate.bleu_score import sentence_bleu, SmoothingFunction
    from nltk.tokenize import word_tokenize
except ImportError:
    print("ERROR: nltk not installed. Run: pip install nltk")
    sys.exit(1)

try:
    from rouge_score import rouge_scorer as _rouge_mod
except ImportError:
    print("ERROR: rouge-score not installed. Run: pip install rouge-score")
    sys.exit(1)

for _tok in ("punkt_tab", "punkt"):
    try:
        nltk.data.find(f"tokenizers/{_tok}")
    except LookupError:
        nltk.download(_tok, quiet=True)


def _normalise(text: str) -> str:
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"[₹#_]", " ", text)
    return re.sub(r"\s+", " ", text.lower()).strip()


def _tokenise(text: str) -> list[str]:
    try:
        return word_tokenize(_normalise(text))
    except Exception:
        return _normalise(text).split()


def compute_bleu(hyp: str, ref: str) -> dict[str, float]:
    h, r = _tokenise(hyp), _tokenise(ref)
    if not h or not r:
        return {k: 0.0 for k in ("bleu_1", "bleu_2", "bleu_3", "bleu_4", "bleu_avg")}
    sf = SmoothingFunction().method1
    weights = {
        "bleu_1": (1, 0, 0, 0),
        "bleu_2": (0.5, 0.5, 0, 0),
        "bleu_3": (0.33, 0.33, 0.34, 0),
        "bleu_4": (0.25, 0.25, 0.25, 0.25),
    }
    scores = {k: round(sentence_bleu([r], h, weights=w, smoothing_function=sf), 4)
              for k, w in weights.items()}
    scores["bleu_avg"] = round(sum(scores[k] for k in ("bleu_1","bleu_2","bleu_3","bleu_4")) / 4, 4)
    return scores


def compute_rouge(hyp: str, ref: str) -> dict[str, dict[str, float]]:
    scorer = _rouge_mod.RougeScorer(["rouge1", "rouge2", "rougeL"], use_stemmer=False)
    result = scorer.score(_normalise(ref), _normalise(hyp))
    return {
        m: {"precision": round(result[m].precision, 4),
            "recall":    round(result[m].recall, 4),
            "fmeasure":  round(result[m].fmeasure, 4)}
        for m in ("rouge1", "rouge2", "rougeL")
    }


# ---------------------------------------------------------------------------
# CAG evaluator — mock GeneratedDocument from PDF text
# ---------------------------------------------------------------------------

from src.evaluation.evaluator import evaluate_cag_document  # noqa: E402


@dataclass
class _MockDoc:
    """Minimal stand-in for GeneratedDocument."""
    content: str
    body: str
    citations: list = field(default_factory=list)
    ungrounded_clauses: list = field(default_factory=list)
    run_id: str = "7c040c68-a430-4009-b405-d92a28705728"
    doc_type: str = "sale_deed"
    pipeline_variant: str = "CAG"


# Fact pattern extracted from the document
SALE_DEED_FACTS = {
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

# Template slots expected in a sale deed
SALE_DEED_SLOTS = [
    "PARTIES_CLAUSE",
    "RECITALS",
    "OPERATIVE_CLAUSE_1",
    "OPERATIVE_CLAUSE_2",
    "OPERATIVE_CLAUSE_3",
    "SCHEDULE_OF_PROPERTY",
    "ATTESTATION",
]

# Reference sale deed (gold standard for BLEU/ROUGE)
SALE_DEED_REFERENCE = """\
THIS SALE DEED is executed at Pune on this 23rd day of March 2026.
PARTIES
VENDOR: Eric Siq, residing at Kiran Samruddhi B, Sus Gaon, Pune, Maharashtra.
PURCHASER: Abhav Bhanot, residing at Balaji Whitefield, Sus Gaon, Pune, Maharashtra.
RECITALS
WHEREAS the Vendor is the absolute owner of the immovable property bearing Survey Number 5464489
situated in the village of Sus Gaon, Taluka Pirangut, District Pune, having an area of 5000 square meters.
WHEREAS the Vendor has agreed to sell the said property to the Purchaser for a consideration of
Rs. 10,00,000 (Rupees Ten Lakhs only).
WHEREAS the Purchaser has paid an advance of Rs. 1,00,000 (Rupees One Lakh only).
WHEREAS the balance consideration shall be paid through RTGS/NEFT.
WHEREAS the registration shall be done at the Baner Registrar Office.
OPERATIVE CLAUSE 1
The Vendor hereby sells, transfers and conveys unto the Purchaser the property bearing Survey Number
5464489 situated in the village of Sus Gaon, Taluka Pirangut, District Pune, measuring 5000 square meters,
for a total consideration of Rs. 10,00,000 (Rupees Ten Lakhs only).
As per Registration Act 1908 Section 17(1)(b) this sale deed requires compulsory registration.
OPERATIVE CLAUSE 2
The Vendor declares that the property is free from all encumbrances and that the Vendor has the absolute
right to sell the said property to the Purchaser.
As per Transfer of Property Act 1882 Section 54 sale of immovable property.
OPERATIVE CLAUSE 3
The Vendor hereby transfers all right, title and interest in the property to the Purchaser.
SCHEDULE OF PROPERTY
Survey Number 5464489
Village Sus Gaon, Taluka Pirangut, District Pune, Maharashtra.
Area 5000 square meters open plot of land.
Gram Panchayat limits of Sus Gaon.
North bounded by land of Shri X.
South bounded by land of Shri Y.
East bounded by land of Shri Z.
West bounded by land of Shri W.
ATTESTATION
IN WITNESS WHEREOF the parties have executed this Sale Deed on 23rd March 2026 at Baner Registrar Office Pune.
VENDOR: Eric Siq
PURCHASER: Abhav Bhanot
WITNESS 1: Asho Shibu
WITNESS 2: Ameya Tipnis
As per Maharashtra Land Revenue Code 1966 and Registration Act 1908.
"""


# ---------------------------------------------------------------------------
# Detect grounded / ungrounded citations from PDF text
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
# Report
# ---------------------------------------------------------------------------

_W = 60

def _bar(v: float) -> str:
    filled = int(round(v * _W))
    return "[" + "#" * filled + "-" * (_W - filled) + f"] {v:.4f}"


def _quality(bleu_avg: float, rouge_l: float) -> str:
    s = (bleu_avg + rouge_l) / 2
    if s >= 0.45: return "Excellent"
    if s >= 0.30: return "Good"
    if s >= 0.15: return "Fair"
    return "Low — vocabulary diverges from reference"


def print_report(pdf_path: str, text: str, bleu: dict, rouge: dict, cag_result) -> dict:
    sep  = "=" * 70
    thin = "-" * 70
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        sep,
        "  Maharashtra Legal Document — Single PDF Evaluation",
        f"  File      : {pdf_path}",
        f"  Timestamp : {ts}",
        f"  Tokens    : {len(_tokenise(text))} (generated)  /  "
        f"{len(_tokenise(SALE_DEED_REFERENCE))} (reference)",
        sep,
        "",
        "  BLEU  (vs. sale deed reference)",
        f"    BLEU-1  {_bar(bleu['bleu_1'])}",
        f"    BLEU-2  {_bar(bleu['bleu_2'])}",
        f"    BLEU-3  {_bar(bleu['bleu_3'])}",
        f"    BLEU-4  {_bar(bleu['bleu_4'])}",
        f"    Avg     {_bar(bleu['bleu_avg'])}",
        "",
        "  ROUGE",
    ]
    for mk, label in [("rouge1","ROUGE-1"),("rouge2","ROUGE-2"),("rougeL","ROUGE-L")]:
        m = rouge[mk]
        lines.append(f"    {label}  P={m['precision']:.4f}  R={m['recall']:.4f}  F={m['fmeasure']:.4f}")

    lines += [
        "",
        f"  Quality : {_quality(bleu['bleu_avg'], rouge['rougeL']['fmeasure'])}",
        thin,
        "",
        "  CAG EVALUATOR  (Tier 1 + Tier 2)",
        thin,
    ]

    t1 = cag_result.tier1
    t2 = cag_result.tier2
    lines += [
        f"  cache_hit_rate           : {t1.cache_hit_rate:.4f}  "
        f"(grounded={t1.grounded_count}, ungrounded={t1.ungrounded_count})",
        f"  slot_fill_rate           : {t1.slot_fill_rate:.4f}  "
        f"(filled={len(t1.filled_slots)}, missing={len(t1.missing_slots)})",
        f"  fact_fidelity_score      : {t1.fact_fidelity_score:.4f}  "
        f"(matched={len(t1.matched_fields)}, missing={len(t1.missing_fields)})",
        f"  latency_seconds          : {t1.latency_seconds:.2f}  (N/A — pre-generated PDF)",
        "",
        "  Tier 2 (structural / citation quality)",
        f"  section_completeness     : {t2.section_completeness:.4f}",
        f"  citation_fmt_compliance  : {t2.citation_format_compliance:.4f}  "
        f"(compliant={t2.compliant_citations}/{t2.total_citations})",
        f"  jurisdictional_accuracy  : {t2.jurisdictional_accuracy:.4f}  "
        f"(jurisdictional_citations={t2.jurisdictional_citations})",
        "",
        "  Matched fact fields : " + ", ".join(t1.matched_fields) if t1.matched_fields else "  Matched fact fields : (none)",
        "  Missing fact fields : " + ", ".join(t1.missing_fields) if t1.missing_fields else "  Missing fact fields : (none)",
        "  Missing slots       : " + ", ".join(t1.missing_slots) if t1.missing_slots else "  Missing slots       : (none)",
        sep,
    ]

    report = "\n".join(lines)
    print(report)

    return {
        "pdf": pdf_path,
        "timestamp": ts,
        "bleu": bleu,
        "rouge": rouge,
        "cag": cag_result.summary(),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/evaluate_single_pdf.py <path_to_pdf>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    print(f"\nExtracting text from: {pdf_path}")
    text = extract_pdf_text(pdf_path)

    if not text.strip():
        print("ERROR: Could not extract text from PDF.")
        sys.exit(1)

    print(f"Extracted {len(text)} characters.\n")

    # BLEU / ROUGE
    bleu  = compute_bleu(text, SALE_DEED_REFERENCE)
    rouge = compute_rouge(text, SALE_DEED_REFERENCE)

    # CAG evaluator — build mock doc
    grounded_citations, ungrounded_lines = _parse_citations(text)
    mock_doc = _MockDoc(
        content=text,
        body=text,
        citations=grounded_citations,
        ungrounded_clauses=ungrounded_lines,
    )

    cag_result = evaluate_cag_document(
        doc=mock_doc,
        fact_pattern=SALE_DEED_FACTS,
        template_slots=SALE_DEED_SLOTS,
        latency_seconds=0.0,
    )

    result = print_report(pdf_path, text, bleu, rouge, cag_result)

    # Save JSON
    out_path = ROOT / "output" / "evaluation_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nJSON saved → {out_path.relative_to(ROOT)}\n")


if __name__ == "__main__":
    main()
