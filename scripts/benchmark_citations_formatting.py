"""
Benchmark script for Phase 1.5 citation formatting and underlining latency.

Verifies:
  - Citation formatting overhead ≤ 50ms per document
  - Underlining overhead ≤ 100ms per document

Also generates a sample Sale Deed to visually validate output.
"""
from __future__ import annotations

import statistics
import time

from src.generation.citation_formatter import (
    assign_reference_numbers,
    build_citation_index,
    collect_citations,
    replace_markers,
)
from src.generation.formatting_engine import get_underline_ranges, underline_parties_pdf_html

# ---------------------------------------------------------------------------
# Sample document (realistic Sale Deed with citation markers)
# ---------------------------------------------------------------------------

SAMPLE_SALE_DEED = """\
SALE DEED

THIS SALE DEED is executed on this 10th day of April 2026 at Mumbai, Maharashtra.

PARTIES:

Vendor Shri Rajesh Kumar Sharma, son of Shri Mohan Lal Sharma, aged 52 years,
residing at Flat No. 4B, Shivaji Nagar, Mumbai - 400 001 (hereinafter referred
to as "the Vendor").

Purchaser Smt Priya Anil Patel, wife of Shri Anil Ramesh Patel, aged 38 years,
residing at 12, Ganesh Colony, Pune - 411 001 (hereinafter referred to as
"the Purchaser").

RECITALS:

The Vendor is the absolute owner of the property bearing Survey No. 123/4A,
situated at Village Andheri, Taluka Andheri, District Mumbai Suburban,
admeasuring 1200 sq. ft., more particularly described in the Schedule below.

The Vendor has agreed to sell and the Purchaser has agreed to purchase the
said property for a total consideration of Rs. 85,00,000/- (Rupees Eighty-Five
Lakhs only).

The transfer of immovable property is governed by [CITE:Transfer of Property Act, 1882, Section 54].
Registration of this deed is mandatory under [CITE:Registration Act, 1908, Section 17(1)(b)].
Stamp duty is payable as per [CITE:Maharashtra Stamp Act, 1958, Article 25, Maharashtra Legislature].

NOW THIS DEED WITNESSETH:

1. The Vendor hereby sells, transfers, and conveys to the Purchaser all rights,
   title, and interest in the said property [CITE:Transfer of Property Act, 1882, Section 54].

2. The total consideration of Rs. 85,00,000/- has been paid by the Purchaser
   to the Vendor, receipt whereof the Vendor hereby acknowledges.

3. The Vendor covenants that the property is free from all encumbrances,
   charges, and claims of any nature whatsoever.

4. The Purchaser shall be entitled to peaceful possession of the property
   from the date of execution of this deed.

IT IS HEREBY AGREED that any dispute arising out of this deed shall be
subject to the jurisdiction of courts at Mumbai.

IN CONSIDERATION WHEREOF the parties have signed this deed on the day and
year first above written.

SCHEDULE OF PROPERTY:

Survey No. 123/4A, Village Andheri, Taluka Andheri, District Mumbai Suburban,
admeasuring 1200 sq. ft., bounded as follows:
  North: Road
  South: Survey No. 123/3
  East:  Survey No. 124/1
  West:  Survey No. 122/5

WITNESSES:
1. ___________________________
2. ___________________________

Vendor Shri Rajesh Kumar Sharma: ___________________________
Purchaser Smt Priya Anil Patel:  ___________________________
"""

ITERATIONS = 200


# ---------------------------------------------------------------------------
# Benchmark helpers
# ---------------------------------------------------------------------------

def benchmark_citation_formatting(text: str, n: int) -> tuple[float, float, float]:
    """Return (mean_ms, median_ms, p95_ms) for citation formatting over n runs."""
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        _, markers = collect_citations(text)
        ref_map = assign_reference_numbers(markers)
        replaced = replace_markers(text, ref_map)
        index = build_citation_index(markers)
        _ = replaced + "\n\n" + index
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.mean(times), statistics.median(times), sorted(times)[int(n * 0.95)]


def benchmark_underlining(text: str, n: int) -> tuple[float, float, float]:
    """Return (mean_ms, median_ms, p95_ms) for underlining over n runs."""
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        ranges = get_underline_ranges(text, "sale_deed")
        _ = underline_parties_pdf_html(text, ranges)
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.mean(times), statistics.median(times), sorted(times)[int(n * 0.95)]


# ---------------------------------------------------------------------------
# Generate and display the test document
# ---------------------------------------------------------------------------

def generate_test_document(text: str) -> str:
    """Run the full citation + formatting pipeline and return the final document."""
    # Step 1: Citation pipeline
    _, markers = collect_citations(text)
    ref_map = assign_reference_numbers(markers)
    replaced = replace_markers(text, ref_map)
    index = build_citation_index(markers)
    doc_with_citations = replaced + "\n\n" + index

    # Step 2: Underlining (PDF/HTML representation)
    ranges = get_underline_ranges(doc_with_citations, "sale_deed")
    final_doc = underline_parties_pdf_html(doc_with_citations, ranges)

    return final_doc


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("PHASE 1.5 — CITATIONS & FORMATTING BENCHMARK")
    print("=" * 70)

    # --- Latency benchmarks ---
    print(f"\nRunning {ITERATIONS} iterations each...\n")

    c_mean, c_median, c_p95 = benchmark_citation_formatting(SAMPLE_SALE_DEED, ITERATIONS)
    u_mean, u_median, u_p95 = benchmark_underlining(SAMPLE_SALE_DEED, ITERATIONS)

    CITATION_LIMIT_MS = 50.0
    UNDERLINE_LIMIT_MS = 100.0

    citation_ok = c_p95 <= CITATION_LIMIT_MS
    underline_ok = u_p95 <= UNDERLINE_LIMIT_MS

    print(f"{'Metric':<35} {'Mean':>8} {'Median':>8} {'P95':>8}  {'Limit':>8}  {'Status':>8}")
    print("-" * 80)
    print(
        f"{'Citation formatting (ms)':<35} {c_mean:>8.2f} {c_median:>8.2f} {c_p95:>8.2f}"
        f"  {CITATION_LIMIT_MS:>8.0f}  {'✓ PASS' if citation_ok else '✗ FAIL':>8}"
    )
    print(
        f"{'Underlining / PDF-HTML (ms)':<35} {u_mean:>8.2f} {u_median:>8.2f} {u_p95:>8.2f}"
        f"  {UNDERLINE_LIMIT_MS:>8.0f}  {'✓ PASS' if underline_ok else '✗ FAIL':>8}"
    )

    # --- Test document ---
    print("\n" + "=" * 70)
    print("GENERATED TEST DOCUMENT (Sale Deed — with citations & underlining)")
    print("=" * 70)
    print("(Underlined spans shown with <u>...</u> tags as they appear in PDF/HTML output)\n")
    print(generate_test_document(SAMPLE_SALE_DEED))

    # --- Summary ---
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    if citation_ok and underline_ok:
        print("✓ All latency requirements met.")
    else:
        if not citation_ok:
            print(f"✗ Citation formatting P95 ({c_p95:.2f}ms) exceeds 50ms limit.")
        if not underline_ok:
            print(f"✗ Underlining P95 ({u_p95:.2f}ms) exceeds 100ms limit.")

    raise SystemExit(0 if (citation_ok and underline_ok) else 1)
