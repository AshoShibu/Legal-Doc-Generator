"""
Integration test: engine + template_mapper + evaluator + app.py syntax.
Run from workspace root: python scripts/_test_frontend_integration.py
"""
import sys, re, ast, os
sys.path.insert(0, ".")
# Force UTF-8 output on Windows
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print("=" * 60)
print("INTEGRATION TEST: engine + template_mapper + evaluator")
print("=" * 60)

errors = []

# ── 1. Imports ────────────────────────────────────────────────────────────────
print("\n[1] Imports")
try:
    from src.cag.engine import (
        _build_slot_prompt, _assemble_from_slots, BACKEND_MODEL_TAGS, Citation, LegalCache,
    )
    from src.generation.template_mapper import TemplateRegistry, DEFAULT_SCHEMA_SLOTS
    from src.generation.document_generator import GeneratedDocument
    from src.cag.cache_loader import load_manifest
    from src.evaluation.evaluator import evaluate_cag_document
    print("  OK: all imports successful")
except Exception as e:
    errors.append(f"Import failed: {e}")
    print(f"  FAIL: {e}")
    sys.exit(1)

# ── 2. Template registry ──────────────────────────────────────────────────────
print("\n[2] Template registry - slot extraction")
DOC_TYPES = [
    "sale_deed", "mortgage_deed", "power_of_attorney", "leave_and_license",
    "gift_deed", "conveyance_deed", "affidavit",
]
registry = TemplateRegistry()
slot_map = {}
for doc in DOC_TYPES:
    try:
        tmpl = registry.get_template(doc)
        slots = [s.name for s in sorted(tmpl.slots, key=lambda s: s.position)]
        slot_map[doc] = slots
        print(f"  OK: {doc} -> {len(slots)} slots")
    except Exception as e:
        errors.append(f"Template {doc}: {e}")
        print(f"  FAIL: {doc}: {e}")
        slot_map[doc] = list(DEFAULT_SCHEMA_SLOTS)

# ── 3. _build_slot_prompt ─────────────────────────────────────────────────────
print("\n[3] _build_slot_prompt - all slot/doc-type combinations")

class FakeEntry:
    def __init__(self, name):
        self.name = name
        self.content = f"Content of {name}"
        self.tokens = 200
        self.priority = "high"

entries = [
    FakeEntry("Transfer of Property Act, 1882"),
    FakeEntry("Maharashtra Stamp Act, 1958"),
    FakeEntry("Registration Act, 1908"),
    FakeEntry("Maharashtra Rent Control Act, 1999"),
]

FULL_INTAKE = {
    "seller_name": "Ramesh Patil", "seller_address": "123 MG Road, Pune 411001",
    "buyer_name": "Suresh Shah", "buyer_address": "456 FC Road, Pune 411004",
    "mortgagor_name": "Ramesh Patil", "mortgagor_address": "123 MG Road, Pune",
    "mortgagee_name": "SBI Bank", "mortgagee_address": "Main Branch, Pune",
    "principal_name": "Ramesh Patil", "principal_address": "123 MG Road, Pune",
    "attorney_name": "Suresh Shah", "attorney_address": "456 FC Road, Pune",
    "poa_type": "Special Power of Attorney",
    "licensor_name": "Ramesh Patil", "licensor_address": "123 MG Road, Pune",
    "licensee_name": "Suresh Shah", "licensee_address": "456 FC Road, Pune",
    "commencement_date": "1 April 2026", "agreement_duration_months": "11",
    "monthly_license_fee": "42000", "security_deposit": "252000",
    "donor_name": "Ramesh Patil", "donor_address": "123 MG Road, Pune",
    "donee_name": "Suresh Shah", "donee_address": "456 FC Road, Pune",
    "conveyor_name": "ABC Developers", "conveyor_address": "Developer Office, Thane",
    "transferee_name": "XYZ CHS", "transferee_address": "Society Office, Thane",
    "deponent_name": "Ramesh Patil", "deponent_age": "42",
    "deponent_occupation": "Farmer", "deponent_address": "Village Ratnagiri",
    "notarisation_authority": "Notary Public Ratnagiri",
    "place_of_execution": "Ratnagiri", "execution_date": "12 March 2026",
    "registration_office": "Sub-Registrar, Pune",
    "Survey_Number": "123/4A", "area": "405 sq.m",
    "consideration_amount": "4500000", "loan_amount": "4000000",
    "mahaRERA_number": "P51700025431",
}
fact_pattern = {"intake": FULL_INTAKE, "ocr_fields": {"Survey_Number": "123/4A"}}

prompt_count = 0
for doc, slots in slot_map.items():
    for slot in slots:
        try:
            prompt = _build_slot_prompt(slot, fact_pattern, "cache context", doc, entries)
            assert "SECTION TO WRITE" in prompt
            assert "FACTS" in prompt
            assert len(prompt) > 150
            prompt_count += 1
        except Exception as e:
            errors.append(f"_build_slot_prompt {doc}/{slot}: {e}")
            print(f"  FAIL: {doc}/{slot}: {e}")

print(f"  OK: {prompt_count} prompts generated without errors")

# ── 4. _assemble_from_slots ───────────────────────────────────────────────────
print("\n[4] _assemble_from_slots - section_completeness simulation")
EXPECTED = ["PARTIES", "RECITALS", "OPERATIVE CLAUSE", "SCHEDULE", "ATTESTATION"]
for doc, slots in slot_map.items():
    slot_contents = {s: f"Content for {s} in {doc}." for s in slots}
    body = _assemble_from_slots(slot_contents, slots, doc, entries)
    present = sum(1 for h in EXPECTED
                  if re.search(rf"(?:^|\n){re.escape(h)}", body, re.IGNORECASE))
    sc = present / len(EXPECTED)
    status = "OK  " if sc == 1.0 else "FAIL"
    print(f"  {status}: {doc} section_completeness={sc:.1f} ({present}/{len(EXPECTED)})")
    if sc < 1.0:
        missing = [h for h in EXPECTED
                   if not re.search(rf"(?:^|\n){re.escape(h)}", body, re.IGNORECASE)]
        errors.append(f"{doc}: missing headings {missing}")

# ── 5. Cache manifests ────────────────────────────────────────────────────────
print("\n[5] Cache manifests - load check")
import logging
logging.disable(logging.WARNING)  # suppress section_hint warnings for clean output
for doc in DOC_TYPES:
    try:
        result = load_manifest(doc, "groq_llama3_8b")
        stamp = any("stamp act" in e.name.lower() for e in result.entries)
        tpa   = any("transfer of property" in e.name.lower() for e in result.entries)
        print(f"  OK: {doc} - {len(result.entries)} entries, "
              f"stamp_act={'YES' if stamp else 'NO'}, "
              f"tpa={'YES' if tpa else 'NO'}, "
              f"omitted={result.omitted}")
    except Exception as e:
        errors.append(f"load_manifest {doc}: {e}")
        print(f"  FAIL: {doc}: {e}")
logging.disable(logging.NOTSET)

# ── 6. Evaluator ──────────────────────────────────────────────────────────────
print("\n[6] Evaluator - mock document scoring")
MOCK_DOC = """THIS SALE DEED

PARTIES

This Sale Deed is made between Ramesh Patil (Vendor) and Suresh Shah (Purchaser).
[Transfer of Property Act, 1882] Section 54, Parliament of India, 1882

RECITALS

WHEREAS the Vendor is the absolute owner of Survey No. 123/4A, Village Sus, Taluka Mulshi, District Pune.
[Registration Act, 1908] Section 17, Parliament of India, 1908

OPERATIVE CLAUSE 1

The Vendor agrees to sell the property for Rs.45,00,000 (Rupees Forty-Five Lakhs only).
Stamp duty is payable under Maharashtra Stamp Act, 1958, Article 25, Schedule I.
[Maharashtra Stamp Act, 1958] Article 25 (Schedule I), Maharashtra Legislature, 1958

OPERATIVE CLAUSE 2

The property is free from all encumbrances.
[Transfer of Property Act, 1882] Section 55, Parliament of India, 1882

SCHEDULE OF PROPERTY

The property is Survey No. 123/4A, area 405 sq.m., Village Sus, Taluka Mulshi, District Pune.
North: Survey No. 122, South: Road, East: Survey No. 124, West: Survey No. 121.

ATTESTATION

IN WITNESS WHEREOF the parties have signed on 20 March 2026 before the Sub-Registrar, Mulshi, Pune.
"""

try:
    class MockCacheEntry:
        def __init__(self, name):
            self.name = name; self.content = "content"; self.tokens = 100; self.priority = "high"

    mock_cache = LegalCache(
        documents=[
            MockCacheEntry("Transfer of Property Act, 1882"),
            MockCacheEntry("Maharashtra Stamp Act, 1958"),
            MockCacheEntry("Registration Act, 1908"),
        ],
        total_tokens=300, llm_backend="groq_llama3_8b",
        session_id="test-session", omitted_documents=[], document_type="sale_deed",
    )

    mock_citations = [
        Citation("Transfer of Property Act, 1882", "54", "1882", 0, "Transfer of Property Act, 1882"),
        Citation("Maharashtra Stamp Act, 1958", "25", "1958", 2, "Maharashtra Stamp Act, 1958"),
        Citation("Registration Act, 1908", "17", "1908", 1, "Registration Act, 1908"),
    ]

    mock_doc = GeneratedDocument(
        content=MOCK_DOC,
        header="GENERATED BY: Test",
        body=MOCK_DOC,
        citation_index="[1] Transfer of Property Act, 1882",
        citations=mock_citations,
        ungrounded_clauses=[],
        high_hallucination_risk=False,
        run_id="test-run",
        doc_type="sale_deed",
        pipeline_variant="cag",
    )
    # Attach cache (evaluator reads doc.cache)
    mock_doc.cache = mock_cache

    result = evaluate_cag_document(
        doc=mock_doc,
        fact_pattern=fact_pattern,
        template_slots=list(slot_map["sale_deed"]),
        latency_seconds=5.0,
    )
    metrics = result.summary()
    print(f"  OK: evaluation completed")
    for k in ["cache_hit_rate", "slot_fill_rate", "section_completeness",
              "citation_format_compliance", "jurisdictional_accuracy"]:
        v = metrics.get(k, "N/A")
        print(f"     {k}: {v:.3f}" if isinstance(v, float) else f"     {k}: {v}")
except Exception as e:
    errors.append(f"Evaluator: {e}")
    print(f"  FAIL: {e}")

# ── 7. app.py syntax ──────────────────────────────────────────────────────────
print("\n[7] Backend API import")
try:
    import backend.main  # noqa: F401
    print("  OK: backend.main imports without errors")
except Exception as e:
    errors.append(f"backend.main: {e}")
    print(f"  FAIL: {e}")

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
if errors:
    print(f"RESULT: {len(errors)} FAILURE(S)")
    for e in errors:
        print(f"  - {e}")
else:
    print("RESULT: ALL INTEGRATION TESTS PASSED")
print("=" * 60)
