#!/usr/bin/env python3
"""
scripts/evaluate_documents.py

Evaluates the individually generated legal documents using BLEU and ROUGE metrics.

Strategy:
  - Hypothesis : the generated .txt file from output/individual/{doc_type}/
  - Reference  : the stub/template draft embedded in run_phase1_workflow.py
                 (the gold-standard Maharashtra legal draft for each doc type)

  BLEU and ROUGE measure how closely the LLM-generated document matches
  the structured reference draft in terms of legal vocabulary, clause
  structure, and key field coverage.

Output:
  - Console report (formatted table)
  - output/evaluation_results.txt  (plain text report)
  - output/evaluation_results.json (machine-readable scores)

Usage:
    python scripts/evaluate_documents.py
"""
from __future__ import annotations

import json
import re
import sys
import warnings
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Dependency imports
# ---------------------------------------------------------------------------

try:
    import nltk
    from nltk.translate.bleu_score import sentence_bleu, SmoothingFunction
    from nltk.tokenize import word_tokenize
    _NLTK_OK = True
except ImportError:
    _NLTK_OK = False
    print("ERROR: nltk not installed. Run: pip install nltk")
    sys.exit(1)

try:
    from rouge_score import rouge_scorer as _rouge_scorer_mod
    _ROUGE_OK = True
except ImportError:
    _ROUGE_OK = False
    print("ERROR: rouge-score not installed. Run: pip install rouge-score")
    sys.exit(1)

# Ensure NLTK punkt tokenizer is available
try:
    nltk.data.find("tokenizers/punkt_tab")
except LookupError:
    nltk.download("punkt_tab", quiet=True)
try:
    nltk.data.find("tokenizers/punkt")
except LookupError:
    nltk.download("punkt", quiet=True)

# ---------------------------------------------------------------------------
# Reference drafts — gold-standard Maharashtra legal text per document type
# These are the structured reference drafts used as evaluation targets.
# ---------------------------------------------------------------------------

REFERENCES: dict[str, str] = {
    "mortgage_deed": """\
SIMPLE MORTGAGE DEED
THIS SIMPLE MORTGAGE DEED is executed at Solapur on this 10th day of March 2026.
PARTIES
MORTGAGOR: Suresh Narayan Patil, aged about 45 years, Indian National, permanently
residing at House No. 7, Ganesh Peth, Solapur 413 001, Maharashtra.
MORTGAGEE: Bank of Maharashtra, Solapur Main Branch, a body corporate constituted
under the Banking Companies Acquisition and Transfer of Undertakings Act 1970,
having its branch office at Bank of Maharashtra, Main Branch, Vijapur Road,
Solapur 413 003, Maharashtra.
RECITALS
WHEREAS the Mortgagor is the absolute owner of the agricultural land more particularly
described in the Schedule hereunder written as the Mortgaged Property as evidenced by
Village Form 7/12 and mutation entry in his name.
This deed is governed by Transfer of Property Act 1882 Section 58 Simple Mortgage.
WHEREAS the Mortgagor has applied to the Mortgagee for a loan of Rs 40,00,000
Rupees Forty Lakhs only for agricultural development purposes and the Mortgagee has
agreed to advance the said loan subject to the Mortgagor creating a simple mortgage
over the Mortgaged Property.
OPERATIVE CLAUSE 1 LOAN AMOUNT AND DISBURSEMENT
The Mortgagee has agreed to advance and the Mortgagor has received the principal sum
of Rs 40,00,000 Rupees Forty Lakhs only on the date hereof.
As per Registration Act 1908 Section 17 this mortgage deed requires compulsory registration.
OPERATIVE CLAUSE 2 INTEREST AND REPAYMENT
The Loan shall carry interest at the rate of 8.75 percent per annum floating linked to MCLR.
The Loan together with interest shall be repaid by the Mortgagor in 180 months 15 years
by way of Equated Monthly Instalments EMI of Rs 39,860 per month commencing from 01 May 2026.
As per Maharashtra Land Revenue Code 1966 Section 32 the mortgage shall be noted in
the Village Form 7/12.
OPERATIVE CLAUSE 3 NATURE OF MORTGAGE
This is a Simple Mortgage as defined under Transfer of Property Act 1882 Section 58.
Possession remains with Mortgagor.
OPERATIVE CLAUSE 4 COVENANTS OF MORTGAGOR
The Mortgagor hereby covenants that he shall pay all land revenue cesses and taxes
in respect of the Mortgaged Property and shall maintain the Mortgaged Property in good condition.
SCHEDULE OF MORTGAGED PROPERTY
Survey Gat Number Survey No 112/3B Gat No 445
Area 810 sq metres agricultural land
Village Nandal Taluka Solapur North District Solapur Maharashtra
WITNESSES
1. Vijay Ramchandra Shinde
2. Meena Ashok Desai
""",

    "leave_and_license": """\
LEAVE AND LICENSE AGREEMENT
THIS LEAVE AND LICENSE AGREEMENT is executed at Mumbai on this 25th day of March 2026.
LICENSOR: Anita Rajendra Mehta, Indian National, permanently residing at
Flat No 602, Oberoi Gardens, Kandivali East, Mumbai 400 101, Maharashtra.
LICENSEE: Rohit Ashish Kapoor, Indian National, permanently residing at
C/o Kapoor Enterprises, 14 Nariman Point, Mumbai 400 021, Maharashtra.
RECITALS
WHEREAS the Licensor is the absolute owner of the residential premises more
particularly described in the Schedule hereunder written as the Licensed Premises.
This agreement is governed by Maharashtra Rent Control Act 1999 Section 24
leave and license agreements.
WHEREAS the Licensor has agreed to grant and the Licensee has agreed to take on
leave and license basis the Licensed Premises for Residential purposes only.
OPERATIVE CLAUSE 1 GRANT OF LICENSE
The Licensor hereby grants to the Licensee a bare license and not a tenancy or lease
to use and occupy the Licensed Premises for a period of 11 months commencing from 01 April 2026.
As per Registration Act 1908 Section 17 this agreement shall be compulsorily registered.
OPERATIVE CLAUSE 2 LICENSE FEE AND SECURITY DEPOSIT
The Licensee shall pay to the Licensor a monthly license fee of Rs 42,000 per month
payable on or before the 5th day of each calendar month.
The Licensee has paid a refundable security deposit of Rs 2,52,000 equivalent to
6 months license fee to the Licensor.
As per Maharashtra Land Revenue Code 1966 Section 32 the security deposit shall be
refunded within 30 days of vacation of the Licensed Premises.
OPERATIVE CLAUSE 3 LOCK-IN PERIOD AND TERMINATION
This agreement shall have a lock-in period of 6 months from the date of commencement.
Either party may terminate this agreement after the lock-in period by giving 30 days
written notice to the other party.
OPERATIVE CLAUSE 4 PERMITTED USE AND RESTRICTIONS
The Licensed Premises shall be used solely for Residential purposes.
Subletting or sub-licensing is not permitted.
Maintenance responsibility Licensor structural Licensee day-to-day.
SCHEDULE OF LICENSED PREMISES
Premises Address Flat No 401 4th Floor Oberoi Gardens Thakur Village Kandivali East
Mumbai 400 101 Maharashtra
Carpet Area 950 sq ft carpet area
Survey CTS No CTS No 2/A of Village Kandivali
District Mumbai Suburban Maharashtra
WITNESSES
1. Harish Dilip Shah
2. Kavita Nitin Jain
""",

    "gift_deed": """\
GIFT DEED
THIS GIFT DEED is executed at Nashik on this 15th day of March 2026.
DONOR: Vasudha Krishnarao Iyer, aged about 65 years, Indian National, permanently
residing at Bungalow No 5, Saraswati Colony, Nashik Road, Nashik 422 101, Maharashtra.
DONEE: Arjun Vasudha Iyer, aged about 38 years, Indian National, permanently residing
at Flat No 203, Lotus Heights, College Road, Nashik 422 005, Maharashtra.
Mother and Son of the Donor.
RECITALS
WHEREAS the Donor is the absolute and lawful owner of the immovable property more
particularly described in the Schedule hereunder written as the Gifted Property
having acquired the same by inheritance from her late husband.
This deed is governed by Transfer of Property Act 1882 Section 122 definition of gift.
WHEREAS the Donor out of natural love and affection for the Donee being her son
desires to gift the Gifted Property to the Donee without any monetary consideration.
OPERATIVE CLAUSE 1 GIFT AND ACCEPTANCE
The Donor hereby gives grants and transfers by way of gift the Gifted Property to
the Donee absolutely and forever free from all encumbrances.
No conditional gift unconditional gift out of natural love and affection.
The Donee has accepted the gift.
As per Transfer of Property Act 1882 Section 123 this gift deed is required to be registered.
As per Registration Act 1908 Section 17 this deed requires compulsory registration
before the Sub-Registrar of Assurances.
OPERATIVE CLAUSE 2 DELIVERY OF POSSESSION
The Donor has delivered actual physical and vacant possession of the Gifted Property
to the Donee on 15 March 2026 and the Donee has accepted the same.
As per Maharashtra Land Revenue Code 1966 Section 32 the mutation entry shall be
recorded in the name of the Donee in the Village Form 7/12.
OPERATIVE CLAUSE 3 TITLE AND ENCUMBRANCE
The Donor hereby covenants that the Gifted Property is free from all encumbrances
mortgages charges liens and claims of any nature whatsoever.
SCHEDULE OF GIFTED PROPERTY
Survey Gat Number Survey No 34/2 Gat No 210
Area 405 sq metres
Village Nashik Road Taluka Nashik District Nashik Maharashtra
WITNESSES
1. Suresh Balaji Kulkarni
2. Lata Mohan Deshpande
""",

    "conveyance_deed": """\
CONVEYANCE DEED
THIS CONVEYANCE DEED is executed at Thane on this 18th day of March 2026.
DEVELOPER PROMOTER: Godrej Properties Limited, a company incorporated under the
Companies Act 2013, having its registered office at Godrej One, Pirojshanagar,
Eastern Express Highway, Vikhroli East, Mumbai 400 079, Maharashtra.
MahaRERA Registration No P51700025431.
PURCHASER: Nikhil Sanjay Wagh, Indian National, permanently residing at
Flat No 1204, Tower B, Godrej Emerald, Thane West 400 610, Maharashtra.
CO-OPERATIVE HOUSING SOCIETY: Godrej Emerald Co-operative Housing Society Ltd,
a co-operative housing society registered under the Maharashtra Co-operative Societies Act 1960.
RECITALS
WHEREAS the Developer is the owner and developer of the residential project known as
Tower B Godrej Emerald situated on land bearing CTS No 45/A Village Majiwada
District Thane Maharashtra.
This deed is governed by Transfer of Property Act 1882 Section 54 sale and conveyance
of immovable property.
OPERATIVE CLAUSE 1 CONVEYANCE OF FLAT
The Developer hereby conveys transfers and assures unto and in favour of the Purchaser
Flat No 1204 12th Floor Tower B Godrej Emerald Carpet Area 872 sq ft Built-up 1105 sq ft
Common Area Share 233 sq ft absolutely and forever.
Consideration Rs 1,25,00,000 Rupees One Crore Twenty-Five Lakhs only.
Stamp Duty Paid Rs 7,50,000.
As per Registration Act 1908 Section 17 this deed requires compulsory registration.
OPERATIVE CLAUSE 2 MAHAERA COMPLIANCE
MahaRERA Registration P51700025431.
Occupancy Certificate received 12 December 2025.
As per Real Estate Regulation and Development Act 2016 MahaRERA compliance is mandatory.
OPERATIVE CLAUSE 3 SOCIETY MEMBERSHIP
The Purchaser shall become a member of the Society and shall abide by the bye-laws
of the Society as per Maharashtra Co-operative Societies Act 1960.
As per Maharashtra Land Revenue Code 1966 Section 32 mutation entry shall be recorded.
SCHEDULE OF PROPERTY
Flat No 1204 12th Floor Tower B Godrej Emerald
CTS No 45/A Village Majiwada District Thane Maharashtra
Execution Date 18 March 2026
WITNESSES
1. Pradeep Ramesh Tiwari
2. Smita Arun Kulkarni
""",

    "affidavit": """\
AFFIDAVIT
I Mangesh Dattatray Sawant aged 42 years Government Employee Maharashtra State
Electricity Board residing at House No 14, Shivaji Chowk, Ratnagiri 415 612 Maharashtra
do hereby solemnly affirm and state as follows.
PURPOSE: Property Ownership submitted to Sub-Registrar Ratnagiri.
STATEMENT 1
I Mangesh Dattatray Sawant am the absolute and lawful owner of the agricultural land
bearing Survey No 67/1 admeasuring 1.20 Hectares situated at Village Pawas
Taluka Ratnagiri District Ratnagiri Maharashtra.
STATEMENT 2
The said land was inherited by me from my late father Shri Dattatray Vishnu Sawant
who passed away on 05 June 2018 and the mutation entry No 1245 has been duly recorded
in the Village Form 7/12 in my name.
STATEMENT 3
The said property is free from all encumbrances mortgages charges liens attachments
and claims of any nature whatsoever.
STATEMENT 4
No sale deed gift deed or any other instrument of transfer has been executed by me
or my predecessors-in-title in respect of the said property except the present transaction.
STATEMENT 5
I make this affidavit for the purpose of registration of the Sale Deed in favour of
the purchaser and to satisfy the Sub-Registrar as to my title and ownership of the said property.
Notarisation Notary Public Ratnagiri.
Place of Execution Ratnagiri.
Execution Date 12 March 2026.
As per Indian Evidence Act 1872 Section 3 admissibility of affidavits.
As per Registration Act 1908 Section 18 optional registration of affidavits.
As per Maharashtra Land Revenue Code 1966 Section 32 revenue record update.
""",
}

# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def read_txt(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def normalise(text: str) -> str:
    """Lowercase, strip markdown, collapse whitespace."""
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"[₹#_]", " ", text)
    text = text.lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokenise(text: str) -> list[str]:
    try:
        return word_tokenize(normalise(text))
    except Exception:
        return normalise(text).split()


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def compute_bleu(hypothesis: str, reference: str) -> dict[str, float]:
    hyp = tokenise(hypothesis)
    ref = tokenise(reference)
    if not hyp or not ref:
        return {"bleu_1": 0.0, "bleu_2": 0.0, "bleu_3": 0.0, "bleu_4": 0.0, "bleu_avg": 0.0}

    sf = SmoothingFunction().method1
    weights_map = {
        "bleu_1": (1, 0, 0, 0),
        "bleu_2": (0.5, 0.5, 0, 0),
        "bleu_3": (0.33, 0.33, 0.34, 0),
        "bleu_4": (0.25, 0.25, 0.25, 0.25),
    }
    scores = {}
    for key, weights in weights_map.items():
        try:
            scores[key] = round(
                sentence_bleu([ref], hyp, weights=weights, smoothing_function=sf), 4
            )
        except Exception:
            scores[key] = 0.0

    scores["bleu_avg"] = round(
        sum(scores[k] for k in ("bleu_1", "bleu_2", "bleu_3", "bleu_4")) / 4, 4
    )
    return scores


def compute_rouge(hypothesis: str, reference: str) -> dict[str, dict[str, float]]:
    scorer = _rouge_scorer_mod.RougeScorer(
        ["rouge1", "rouge2", "rougeL"], use_stemmer=False
    )
    try:
        result = scorer.score(normalise(reference), normalise(hypothesis))
        return {
            metric: {
                "precision": round(result[metric].precision, 4),
                "recall":    round(result[metric].recall, 4),
                "fmeasure":  round(result[metric].fmeasure, 4),
            }
            for metric in ("rouge1", "rouge2", "rougeL")
        }
    except Exception:
        empty = {"precision": 0.0, "recall": 0.0, "fmeasure": 0.0}
        return {"rouge1": empty, "rouge2": empty, "rougeL": empty}


# ---------------------------------------------------------------------------
# Document discovery
# ---------------------------------------------------------------------------


def find_generated_docs(output_dir: Path) -> dict[str, Path]:
    """Find the most recently generated .txt for each doc type."""
    found: dict[str, Path] = {}
    for doc_type in REFERENCES:
        type_dir = output_dir / doc_type
        if not type_dir.exists():
            continue
        candidates = sorted(
            type_dir.glob(f"*/{doc_type}.txt"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            found[doc_type] = candidates[0]
    return found


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

_BAR_WIDTH = 28


def _bar(value: float) -> str:
    filled = int(round(value * _BAR_WIDTH))
    return "[" + "#" * filled + "-" * (_BAR_WIDTH - filled) + f"] {value:.4f}"


def _interpret(bleu_avg: float, rouge_l_f: float) -> str:
    """Plain-English quality band."""
    score = (bleu_avg + rouge_l_f) / 2
    if score >= 0.45:
        return "Excellent — strong structural and vocabulary alignment"
    if score >= 0.30:
        return "Good — solid legal structure, minor vocabulary divergence"
    if score >= 0.15:
        return "Fair — correct document type, some clause variation"
    return "Low — document generated but vocabulary differs from reference"


def build_report(results: list[dict], timestamp: str) -> str:
    lines: list[str] = []
    sep  = "=" * 70
    thin = "-" * 70

    lines += [
        sep,
        "  Maharashtra Legal Document Evaluation Report",
        f"  Generated : {timestamp}",
        f"  Documents : {len(results)} evaluated",
        sep,
    ]

    ok = [r for r in results if r["status"] == "ok"]

    for r in results:
        doc_label = r["doc_type"].replace("_", " ").title()
        lines += ["", f"  [ {doc_label} ]"]
        lines += [f"  File : {r.get('generated_path', 'N/A')}"]
        lines += [f"  Tokens (generated / reference) : "
                  f"{r.get('hyp_tokens', 0)} / {r.get('ref_tokens', 0)}"]

        if r["status"] != "ok":
            lines += [f"  WARNING: {r['status']}", thin]
            continue

        b  = r["bleu"]
        rg = r["rouge"]

        lines += [
            "",
            "  BLEU",
            f"    BLEU-1  {_bar(b['bleu_1'])}",
            f"    BLEU-2  {_bar(b['bleu_2'])}",
            f"    BLEU-3  {_bar(b['bleu_3'])}",
            f"    BLEU-4  {_bar(b['bleu_4'])}",
            f"    Avg     {_bar(b['bleu_avg'])}",
            "",
            "  ROUGE",
        ]
        for mk, label in [("rouge1", "ROUGE-1"), ("rouge2", "ROUGE-2"), ("rougeL", "ROUGE-L")]:
            m = rg[mk]
            lines.append(
                f"    {label}  "
                f"P={m['precision']:.4f}  R={m['recall']:.4f}  F={m['fmeasure']:.4f}"
            )

        quality = _interpret(b["bleu_avg"], rg["rougeL"]["fmeasure"])
        lines += ["", f"  Quality : {quality}", thin]

    # Aggregate
    if ok:
        def mean(keys: list) -> float:
            vals = []
            for r in ok:
                obj = r
                for k in keys:
                    obj = obj[k]
                vals.append(float(obj))
            return sum(vals) / len(vals)

        lines += [
            "",
            sep,
            "  AGGREGATE  (mean across all evaluated documents)",
            sep,
            "",
            "  BLEU",
            f"    BLEU-1  {_bar(mean(['bleu', 'bleu_1']))}",
            f"    BLEU-2  {_bar(mean(['bleu', 'bleu_2']))}",
            f"    BLEU-3  {_bar(mean(['bleu', 'bleu_3']))}",
            f"    BLEU-4  {_bar(mean(['bleu', 'bleu_4']))}",
            f"    Avg     {_bar(mean(['bleu', 'bleu_avg']))}",
            "",
            "  ROUGE",
        ]
        for mk, label in [("rouge1", "ROUGE-1"), ("rouge2", "ROUGE-2"), ("rougeL", "ROUGE-L")]:
            p   = mean(["rouge", mk, "precision"])
            rec = mean(["rouge", mk, "recall"])
            f   = mean(["rouge", mk, "fmeasure"])
            lines.append(f"    {label}  P={p:.4f}  R={rec:.4f}  F={f:.4f}")

        overall_bleu  = mean(["bleu", "bleu_avg"])
        overall_rouge = mean(["rouge", "rougeL", "fmeasure"])
        lines += [
            "",
            f"  Overall quality : {_interpret(overall_bleu, overall_rouge)}",
        ]

    lines += ["", sep, ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    output_dir  = ROOT / "output" / "individual"
    results_txt  = ROOT / "output" / "evaluation_results.txt"
    results_json = ROOT / "output" / "evaluation_results.json"

    print(f"\nScanning {output_dir} for generated documents...")
    generated = find_generated_docs(output_dir)
    print(f"Found {len(generated)} document(s): {', '.join(generated)}\n")

    results: list[dict] = []

    for doc_type, ref_text in REFERENCES.items():
        entry: dict = {"doc_type": doc_type, "status": "ok"}

        if doc_type not in generated:
            entry["status"] = f"No generated .txt found in {output_dir / doc_type}"
            results.append(entry)
            continue

        gen_path = generated[doc_type]
        hypothesis = read_txt(gen_path)

        entry["generated_path"] = str(gen_path.relative_to(ROOT))
        entry["hyp_tokens"] = len(tokenise(hypothesis))
        entry["ref_tokens"] = len(tokenise(ref_text))

        if not hypothesis.strip():
            entry["status"] = "Generated file is empty"
            results.append(entry)
            continue

        print(f"  Evaluating {doc_type}...", end=" ", flush=True)
        entry["bleu"]  = compute_bleu(hypothesis, ref_text)
        entry["rouge"] = compute_rouge(hypothesis, ref_text)
        print(
            f"BLEU-avg={entry['bleu']['bleu_avg']:.4f}  "
            f"ROUGE-L F={entry['rouge']['rougeL']['fmeasure']:.4f}"
        )

        results.append(entry)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report    = build_report(results, timestamp)

    # Print to console
    print("\n" + report)

    # Save plain text report
    results_txt.parent.mkdir(parents=True, exist_ok=True)
    results_txt.write_text(report, encoding="utf-8")
    print(f"  Report saved → {results_txt.relative_to(ROOT)}")

    # Save JSON
    results_json.write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  JSON saved  → {results_json.relative_to(ROOT)}\n")


if __name__ == "__main__":
    main()
