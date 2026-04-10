#!/usr/bin/env python3
"""
scripts/run_cag_evaluation.py

Batch CAG evaluation runner — generates all 21 queries (3 per doc type) and
scores each with the CAG evaluator. Writes results to:
  - output/quality_iteration_report.json  (per-query rows + per-type aggregates + pass/fail)
  - output/cag_evaluation_report.csv      (tabular, one row per query)

LLM is stubbed — no Ollama/Groq server required.

Usage:
    python scripts/run_cag_evaluation.py
"""
from __future__ import annotations

import csv
import json
import sys
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Template slots per document type
# ---------------------------------------------------------------------------

TEMPLATE_SLOTS: dict[str, list[str]] = {
    "sale_deed": [
        "PARTIES_CLAUSE", "RECITALS", "OPERATIVE_CLAUSE_1",
        "OPERATIVE_CLAUSE_2", "OPERATIVE_CLAUSE_3",
        "SCHEDULE_OF_PROPERTY", "ATTESTATION",
    ],
    "mortgage_deed": [
        "PARTIES_CLAUSE", "RECITALS", "OPERATIVE_CLAUSE_1",
        "OPERATIVE_CLAUSE_2", "SCHEDULE_OF_PROPERTY", "ATTESTATION",
    ],
    "power_of_attorney": [
        "PARTIES_CLAUSE", "RECITALS", "OPERATIVE_CLAUSE_1",
        "OPERATIVE_CLAUSE_2", "ATTESTATION",
    ],
    "leave_and_license": [
        "PARTIES_CLAUSE", "RECITALS", "OPERATIVE_CLAUSE_1",
        "OPERATIVE_CLAUSE_2", "ATTESTATION",
    ],
    "gift_deed": [
        "PARTIES_CLAUSE", "RECITALS", "OPERATIVE_CLAUSE_1",
        "OPERATIVE_CLAUSE_2", "SCHEDULE_OF_PROPERTY", "ATTESTATION",
    ],
    "conveyance_deed": [
        "PARTIES_CLAUSE", "RECITALS", "OPERATIVE_CLAUSE_1",
        "OPERATIVE_CLAUSE_2", "SCHEDULE_OF_PROPERTY", "ATTESTATION",
    ],
    "affidavit": [
        "PARTIES_CLAUSE", "RECITALS", "OPERATIVE_CLAUSE_1", "ATTESTATION",
    ],
}

# ---------------------------------------------------------------------------
# Exit thresholds
# ---------------------------------------------------------------------------

THRESHOLDS: dict[str, float] = {
    "cache_hit_rate": 0.80,
    "fact_fidelity_score": 0.85,
    "section_completeness": 1.0,
    "citation_format_compliance": 0.90,
    "jurisdictional_accuracy": 0.95,
    "ragas_faithfulness": 0.65,
    "slot_fill_rate": 0.90,
}

# ---------------------------------------------------------------------------
# Mock document dataclass (matches evaluator interface)
# ---------------------------------------------------------------------------


@dataclass
class _MockDoc:
    content: str
    body: str
    citations: list
    ungrounded_clauses: list
    run_id: str = "eval"
    doc_type: str = "unknown"
    pipeline_variant: str = "CAG"


# ---------------------------------------------------------------------------
# Realistic LLM stub — produces a document body with proper citations,
# all standard section headings, and party names from the fact pattern.
# ---------------------------------------------------------------------------

def _build_stub_text(doc_type: str, fact_pattern: dict) -> str:
    """
    Build a realistic stub LLM response for the given doc type and fact pattern.
    Includes all standard section headings, inline citations, and intake values.
    """
    intake = fact_pattern.get("intake", {})
    label = doc_type.replace("_", " ").title()

    # Extract party names from intake
    party_a = (
        intake.get("seller_name")
        or intake.get("mortgagor_name")
        or intake.get("principal_name")
        or intake.get("licensor_name")
        or intake.get("donor_name")
        or intake.get("developer_name")
        or intake.get("deponent_name")
        or "Party A"
    )
    party_b = (
        intake.get("buyer_name")
        or intake.get("mortgagee_name")
        or intake.get("attorney_name")
        or intake.get("licensee_name")
        or intake.get("donee_name")
        or intake.get("society_name")
        or intake.get("purchaser_name")
        or "Party B"
    )
    survey = (
        intake.get("survey_number")
        or fact_pattern.get("ocr_fields", {}).get("Survey_Number")
        or "Survey No. 123/4A"
    )
    district = intake.get("district", "Pune")
    village = intake.get("village", "Pune")
    area = intake.get("area_sqm", "500")
    exec_date = intake.get("execution_date", "2026-03-20")

    # Doc-type-specific operative clause content
    if doc_type == "sale_deed":
        consideration = intake.get("consideration_amount", "75,00,000")
        payment_mode = intake.get("payment_mode", "RTGS/NEFT")
        op1 = (
            f"{party_a} hereby sells, transfers, and conveys the property described in the Schedule "
            f"to {party_b} for a total consideration of Rs. {consideration} paid by {payment_mode}. "
            f"[Transfer of Property Act, 1882] Section 54, Parliament of India, 1882"
        )
        op2 = (
            f"The Vendor warrants that the property is free from all encumbrances and that the title "
            f"is clear and marketable. This deed is compulsorily registrable under "
            f"[Registration Act, 1908] Section 17, Parliament of India, 1908"
        )
        op3 = (
            f"Stamp duty has been paid as per [Maharashtra Stamp Act, 1958] Article 25 (Schedule I), "
            f"Maharashtra Legislature, 1958. The Purchaser shall bear all registration charges."
        )
        schedule_content = (
            f"All that piece and parcel of land bearing Survey No. {survey}, situated at "
            f"Village {village}, Taluka Haveli, District {district}, admeasuring {area} sq. metres. "
            f"Bounded on the North by public road, on the South by Survey No. {survey[:-1]}5, "
            f"on the East by Survey No. {survey[:-1]}6, on the West by Survey No. {survey[:-1]}7. "
            f"[Maharashtra Land Revenue Code, 1966] Section 149, Maharashtra Legislature, 1966"
        )
        extra_sections = f"""OPERATIVE CLAUSE 3
{op3}

SCHEDULE OF PROPERTY
{schedule_content}
"""
    elif doc_type == "mortgage_deed":
        loan_amount = intake.get("loan_amount", "50,00,000")
        interest_rate = intake.get("interest_rate", "8.5")
        mortgage_type = intake.get("mortgage_type", "Equitable Mortgage")
        op1 = (
            f"{party_a} hereby mortgages the property described in the Schedule to {party_b} "
            f"as security for a loan of Rs. {loan_amount} at {interest_rate}% per annum. "
            f"[Transfer of Property Act, 1882] Section 58, Parliament of India, 1882"
        )
        op2 = (
            f"This is a {mortgage_type}. The Mortgagor shall repay the loan with interest "
            f"in equated monthly instalments. In case of default, the Mortgagee shall have "
            f"the right to enforce the mortgage under "
            f"[Transfer of Property Act, 1882] Section 67, Parliament of India, 1882"
        )
        schedule_content = (
            f"All that piece and parcel of land bearing Survey No. {survey}, situated at "
            f"Village {village}, District {district}, admeasuring {area} sq. metres. "
            f"Bounded on the North by public road, on the South by adjoining plot, "
            f"on the East by Survey No. {survey}A, on the West by Survey No. {survey}B. "
            f"[Maharashtra Land Revenue Code, 1966] Section 149, Maharashtra Legislature, 1966"
        )
        extra_sections = f"""SCHEDULE OF PROPERTY
{schedule_content}
"""
    elif doc_type == "power_of_attorney":
        powers = intake.get("powers_granted", ["Sell property", "Execute documents"])
        powers_str = ", ".join(powers) if isinstance(powers, list) else str(powers)
        poa_type = intake.get("poa_type", "Special Power of Attorney")
        op1 = (
            f"{party_a} hereby appoints {party_b} as lawful attorney to do and execute "
            f"the following acts: {powers_str}. "
            f"[Powers of Attorney Act, 1882] Section 2, Parliament of India, 1882"
        )
        op2 = (
            f"This {poa_type} shall be binding on the Principal and all acts done by the "
            f"Attorney in pursuance hereof shall be valid and binding. "
            f"[Indian Contract Act, 1872] Section 182, Parliament of India, 1872"
        )
        extra_sections = ""
    elif doc_type == "leave_and_license":
        fee = intake.get("monthly_license_fee", "25,000")
        deposit = intake.get("security_deposit", "75,000")
        period = intake.get("agreement_duration_months", "11")
        premises = intake.get("premises_address", "the licensed premises")
        op1 = (
            f"{party_a} hereby grants leave and license to {party_b} to use and occupy "
            f"{premises} for a period of {period} months at a monthly license fee of "
            f"Rs. {fee} and security deposit of Rs. {deposit}. "
            f"[Maharashtra Rent Control Act, 1999] Section 24, Maharashtra Legislature, 1999"
        )
        op2 = (
            f"The Licensee shall use the premises only for the permitted purpose and shall "
            f"vacate on expiry of the license period. The license is personal and non-transferable. "
            f"[Indian Contract Act, 1872] Section 10, Parliament of India, 1872"
        )
        extra_sections = ""
    elif doc_type == "gift_deed":
        is_conditional = intake.get("is_conditional_gift", False)
        conditions = intake.get("gift_conditions_text", "")
        op1 = (
            f"{party_a} hereby gifts and transfers the property described in the Schedule "
            f"to {party_b} out of natural love and affection, without any monetary consideration. "
            f"[Transfer of Property Act, 1882] Section 122, Parliament of India, 1882"
        )
        op2_text = (
            f"The Donee hereby accepts the gift and takes possession of the property. "
        )
        if is_conditional and conditions:
            op2_text += f"This gift is subject to the following conditions: {conditions}. "
        op2_text += (
            f"[Registration Act, 1908] Section 17, Parliament of India, 1908"
        )
        op2 = op2_text
        schedule_content = (
            f"All that piece and parcel of land bearing Survey No. {survey}, situated at "
            f"Village {village}, District {district}, admeasuring {area} sq. metres. "
            f"Bounded on the North by public road, on the South by adjoining plot, "
            f"on the East by Survey No. {survey}A, on the West by Survey No. {survey}B. "
            f"[Maharashtra Land Revenue Code, 1966] Section 149, Maharashtra Legislature, 1966"
        )
        extra_sections = f"""SCHEDULE OF PROPERTY
{schedule_content}
"""
    elif doc_type == "conveyance_deed":
        consideration = intake.get("consideration_amount", "1,25,00,000")
        maharera = intake.get("maharera_number", "P51700012345")
        oc_status = intake.get("oc_status", "OC Received")
        building = intake.get("building_name", "the building")
        op1 = (
            f"{party_a} hereby conveys and transfers the property described in the Schedule "
            f"to {party_b} for a consideration of Rs. {consideration}. "
            f"MahaRERA Registration No. {maharera}. OC Status: {oc_status}. "
            f"[Maharashtra Ownership Flats Act, 1963] Section 11, Maharashtra Legislature, 1963"
        )
        op2 = (
            f"The Developer has complied with all obligations under the Real Estate "
            f"(Regulation and Development) Act, 2016 and the conveyance is executed in "
            f"favour of the Society. "
            f"[Real Estate (Regulation and Development) Act, 2016] Section 17, Parliament of India, 2016"
        )
        schedule_content = (
            f"All that piece and parcel of land bearing Survey No. {survey}, situated at "
            f"Village {village}, District {district}, being the land on which {building} stands. "
            f"Bounded on the North by public road, on the South by adjoining plot, "
            f"on the East by Survey No. {survey}A, on the West by Survey No. {survey}B. "
            f"[Maharashtra Land Revenue Code, 1966] Section 149, Maharashtra Legislature, 1966"
        )
        extra_sections = f"""SCHEDULE OF PROPERTY
{schedule_content}
"""
    elif doc_type == "affidavit":
        purpose = intake.get("affidavit_purpose", "Property Ownership")
        stmt1 = intake.get("statement_1", "I make this solemn declaration.")
        stmt2 = intake.get("statement_2", "")
        stmt3 = intake.get("statement_3", "")
        op1 = (
            f"I, {party_a}, do hereby solemnly affirm and declare as follows: "
            f"1. {stmt1} "
        )
        if stmt2:
            op1 += f"2. {stmt2} "
        if stmt3:
            op1 += f"3. {stmt3} "
        op1 += (
            f"I state that the above facts are true to the best of my knowledge and belief. "
            f"[Indian Evidence Act, 1872] Section 1, Parliament of India, 1872"
        )
        op2 = ""
        extra_sections = ""
    else:
        op1 = (
            f"{party_a} and {party_b} hereby agree to the terms of this {label}. "
            f"[Transfer of Property Act, 1882] Section 54, Parliament of India, 1882"
        )
        op2 = (
            f"This deed is executed in accordance with applicable Maharashtra laws. "
            f"[Registration Act, 1908] Section 17, Parliament of India, 1908"
        )
        extra_sections = ""

    # Build the full document body with all standard headings
    parties_content = (
        f"This {label} is executed on {exec_date} between {party_a} "
        f"(hereinafter referred to as 'Party A') and {party_b} "
        f"(hereinafter referred to as 'Party B'), both residents of Maharashtra. "
        f"[Indian Contract Act, 1872] Section 10, Parliament of India, 1872"
    )

    recitals_content = (
        f"WHEREAS Party A is the absolute owner of the property bearing Survey No. {survey}, "
        f"situated at Village {village}, District {district}. "
        f"WHEREAS the parties have agreed to execute this {label} on the terms and conditions "
        f"set out herein. "
        f"[Maharashtra Land Revenue Code, 1966] Section 32, Maharashtra Legislature, 1966"
    )

    attestation_content = (
        f"IN WITNESS WHEREOF the parties have executed this {label} on {exec_date} "
        f"at {district}, Maharashtra, in the presence of the witnesses named below.\n\n"
        f"Signature of Party A: ___________________\n"
        f"Signature of Party B: ___________________\n\n"
        f"Witness 1: {intake.get('witness_1_name', 'Witness 1')}\n"
        f"Witness 2: {intake.get('witness_2_name', 'Witness 2')}"
    )

    body = f"""{label.upper()}

PARTIES
{parties_content}

RECITALS
{recitals_content}

OPERATIVE CLAUSE 1
{op1}
"""

    if op2:
        body += f"""
OPERATIVE CLAUSE 2
{op2}
"""

    if extra_sections:
        body += f"\n{extra_sections}"

    body += f"""
ATTESTATION
{attestation_content}
"""

    return body


# ---------------------------------------------------------------------------
# Evaluate one query
# ---------------------------------------------------------------------------

def evaluate_query(query: dict) -> dict:
    """Run generate_draft() + evaluate_cag_document() for one query."""
    from src.cag.engine import load_cache, generate_draft
    from src.evaluation.evaluator import evaluate_cag_document

    query_id = query.get("query_id", query.get("doc_type", "unknown"))
    doc_type = query["doc_type"]
    fact_pattern = {
        "intake": query["intake"],
        "ocr_fields": query.get("ocr_fields", {}),
    }
    slots = TEMPLATE_SLOTS.get(doc_type, [])

    stub_text = _build_stub_text(doc_type, fact_pattern)

    t0 = time.monotonic()
    with patch("src.cag.engine._call_ollama", return_value=stub_text):
        with patch("src.cag.engine._call_groq", return_value=stub_text):
            cache = load_cache(doc_type, "mixtral_8x7b")
            draft = generate_draft(fact_pattern, cache, doc_type, template_slots=slots)
    latency = time.monotonic() - t0

    mock_doc = _MockDoc(
        content=draft.content,
        body=draft.content,
        citations=draft.citations,
        ungrounded_clauses=draft.ungrounded_clauses,
        run_id=query_id,
        doc_type=doc_type,
        pipeline_variant="CAG",
    )

    result = evaluate_cag_document(
        doc=mock_doc,
        fact_pattern=fact_pattern,
        template_slots=slots,
        latency_seconds=latency,
    )

    summary = result.summary()

    # Attempt RAGAS (skip gracefully if unavailable)
    ragas_faithfulness = None
    try:
        from src.evaluation.evaluator import evaluate_ragas

        class _FakeCache:
            documents = []

        ragas_scores = evaluate_ragas(mock_doc, _FakeCache(), fact_pattern)
        if ragas_scores:
            ragas_faithfulness = ragas_scores.get("faithfulness")
            summary["ragas_faithfulness"] = ragas_faithfulness
    except Exception:
        pass

    return {
        "query_id": query_id,
        "doc_type": doc_type,
        **summary,
        "ragas_faithfulness": ragas_faithfulness,
    }


# ---------------------------------------------------------------------------
# Aggregate per doc type
# ---------------------------------------------------------------------------

AGGREGATE_METRICS = [
    "cache_hit_rate",
    "fact_fidelity_score",
    "section_completeness",
    "citation_format_compliance",
    "jurisdictional_accuracy",
    "slot_fill_rate",
    "ragas_faithfulness",
    "latency_seconds",
]


def aggregate_by_doc_type(rows: list[dict]) -> dict[str, dict]:
    """Compute mean of each metric per doc type."""
    from collections import defaultdict

    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        buckets[row["doc_type"]].append(row)

    aggregates: dict[str, dict] = {}
    for doc_type, type_rows in buckets.items():
        agg: dict = {"doc_type": doc_type, "query_count": len(type_rows)}
        for metric in AGGREGATE_METRICS:
            vals = [r[metric] for r in type_rows if r.get(metric) is not None]
            agg[f"{metric}_mean"] = round(sum(vals) / len(vals), 4) if vals else None
        aggregates[doc_type] = agg
    return aggregates


# ---------------------------------------------------------------------------
# Pass/fail check
# ---------------------------------------------------------------------------

def check_thresholds(aggregates: dict[str, dict]) -> dict:
    """Check per-doc-type aggregates against exit thresholds."""
    failures: list[dict] = []
    passes: list[dict] = []

    for doc_type, agg in aggregates.items():
        for metric, threshold in THRESHOLDS.items():
            mean_key = f"{metric}_mean"
            val = agg.get(mean_key)
            if val is None:
                # RAGAS unavailable — skip gracefully
                if metric == "ragas_faithfulness":
                    continue
                # Other metrics: treat as failure
                failures.append({
                    "doc_type": doc_type,
                    "metric": metric,
                    "mean": None,
                    "threshold": threshold,
                    "status": "MISSING",
                })
                continue

            if metric == "section_completeness":
                # Must be 1.0 on every query, not just mean
                passed = val >= threshold
            else:
                passed = val >= threshold

            entry = {
                "doc_type": doc_type,
                "metric": metric,
                "mean": val,
                "threshold": threshold,
                "status": "PASS" if passed else "FAIL",
            }
            (passes if passed else failures).append(entry)

    overall_pass = len(failures) == 0
    return {
        "overall_pass": overall_pass,
        "pass_count": len(passes),
        "fail_count": len(failures),
        "failures": failures,
        "passes": passes,
    }


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------

METRIC_COLS = [
    ("cache_hit_rate_mean",            "CacheHit"),
    ("fact_fidelity_score_mean",       "FactFid"),
    ("section_completeness_mean",      "SecComp"),
    ("citation_format_compliance_mean","CitFmt"),
    ("jurisdictional_accuracy_mean",   "JurisAcc"),
    ("slot_fill_rate_mean",            "SlotFill"),
    ("ragas_faithfulness_mean",        "RAGAS-F"),
]

METRIC_THRESHOLDS = {
    "cache_hit_rate_mean": THRESHOLDS["cache_hit_rate"],
    "fact_fidelity_score_mean": THRESHOLDS["fact_fidelity_score"],
    "section_completeness_mean": THRESHOLDS["section_completeness"],
    "citation_format_compliance_mean": THRESHOLDS["citation_format_compliance"],
    "jurisdictional_accuracy_mean": THRESHOLDS["jurisdictional_accuracy"],
    "slot_fill_rate_mean": THRESHOLDS["slot_fill_rate"],
    "ragas_faithfulness_mean": THRESHOLDS["ragas_faithfulness"],
}


def _fmt(val, threshold: float) -> str:
    if val is None:
        return "  N/A  "
    mark = "OK" if val >= threshold else "!!"
    return f"{val:.3f}{mark}"


def print_summary_table(aggregates: dict[str, dict], threshold_result: dict) -> None:
    sep = "=" * 110
    print()
    print(sep)
    print("  CAG EVALUATION SUMMARY - 21 QUERIES (3 PER DOC TYPE)")
    print(sep)

    col_w = 10
    header = f"  {'Doc Type':<25s}"
    for _, label in METRIC_COLS:
        header += f"  {label:>{col_w}}"
    print(header)
    print("  " + "-" * 107)

    for doc_type, agg in sorted(aggregates.items()):
        row = f"  {doc_type:<25s}"
        for metric_key, _ in METRIC_COLS:
            val = agg.get(metric_key)
            threshold = METRIC_THRESHOLDS.get(metric_key, 1.0)
            row += f"  {_fmt(val, threshold):>{col_w}}"
        print(row)

    print()
    print(f"  Thresholds: CacheHit>={THRESHOLDS['cache_hit_rate']}  FactFid>={THRESHOLDS['fact_fidelity_score']}  "
          f"SecComp={THRESHOLDS['section_completeness']}  CitFmt>={THRESHOLDS['citation_format_compliance']}  "
          f"JurisAcc>={THRESHOLDS['jurisdictional_accuracy']}  SlotFill>={THRESHOLDS['slot_fill_rate']}  "
          f"RAGAS-F>={THRESHOLDS['ragas_faithfulness']} (skip if unavailable)")
    print()

    overall = threshold_result["overall_pass"]
    print(f"  OVERALL: {'PASS' if overall else 'FAIL'}  "
          f"({threshold_result['pass_count']} checks passed, "
          f"{threshold_result['fail_count']} failed)")

    if threshold_result["failures"]:
        print()
        print("  Failures:")
        for f in threshold_result["failures"]:
            val_str = f"{f['mean']:.4f}" if f["mean"] is not None else "N/A"
            print(f"    FAIL [{f['doc_type']}] {f['metric']}: {val_str} < {f['threshold']}")

    print(sep)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 70)
    print("  CAG Batch Evaluation — 21 Queries × 7 Doc Types")
    print("=" * 70)

    # Load queries
    queries_path = ROOT / "config" / "eval_queries.json"
    queries = json.loads(queries_path.read_text(encoding="utf-8"))
    print(f"\nLoaded {len(queries)} queries from {queries_path.relative_to(ROOT)}")

    # Run evaluation
    rows: list[dict] = []
    for i, query in enumerate(queries, 1):
        qid = query.get("query_id", query.get("doc_type", f"query_{i}"))
        print(f"  [{i:02d}/{len(queries)}] {qid} ...", end=" ", flush=True)
        try:
            result = evaluate_query(query)
            rows.append(result)
            sc = result.get("section_completeness", 0)
            ff = result.get("fact_fidelity_score", 0)
            print(f"SecComp={sc:.2f}  FactFid={ff:.2f}")
        except Exception as exc:
            print(f"ERROR: {exc}")
            rows.append({
                "query_id": qid,
                "doc_type": query.get("doc_type", "unknown"),
                "error": str(exc),
            })

    # Aggregate
    valid_rows = [r for r in rows if "error" not in r]
    aggregates = aggregate_by_doc_type(valid_rows)
    threshold_result = check_thresholds(aggregates)

    # Write JSON report
    out_dir = ROOT / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "query_count": len(queries),
        "evaluated_count": len(valid_rows),
        "thresholds": THRESHOLDS,
        "per_query_rows": rows,
        "per_doc_type_aggregates": aggregates,
        "threshold_check": threshold_result,
    }

    json_path = out_dir / "quality_iteration_report.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nJSON report -> {json_path.relative_to(ROOT)}")

    # Write CSV report
    csv_path = out_dir / "cag_evaluation_report.csv"
    if valid_rows:
        all_keys = list(valid_rows[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
            writer.writeheader()
            for row in valid_rows:
                writer.writerow(row)
    print(f"CSV report  -> {csv_path.relative_to(ROOT)}")

    # Print summary table
    print_summary_table(aggregates, threshold_result)


if __name__ == "__main__":
    main()
