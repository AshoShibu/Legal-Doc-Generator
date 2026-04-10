"""
Audit and integration test for issues 5-8:
5. Affidavit notarisation block — "on oath", notarisation_authority, NOTARY REG. NO. fallback
6. Sub-Registrar office in attestation — registration_office injected for all deed types
7. Leave and License execution date — commencement_date in header, no [insert date]
8. Conveyance Deed Society address — SOCIETY ADDRESS fallback, no [Insert Address]

Run from workspace root: python scripts/_audit_issues_5_to_8.py
"""
import sys, re, ast
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print("=" * 65)
print("AUDIT: Issues 5-8 — fix presence + integration test")
print("=" * 65)

errors = []

# ── Imports ───────────────────────────────────────────────────────────────────
from src.cag.engine import _build_slot_prompt, _assemble_from_slots, Citation, LegalCache

class E:
    def __init__(self, name):
        self.name = name; self.content = f"Content of {name}"; self.tokens = 200; self.priority = "high"

ENTRIES = [
    E("Transfer of Property Act, 1882"),
    E("Maharashtra Stamp Act, 1958"),
    E("Registration Act, 1908"),
    E("Maharashtra Rent Control Act, 1999"),
    E("Powers of Attorney Act, 1882"),
]

FULL_INTAKE = {
    # affidavit
    "deponent_name": "Ramesh Patil", "deponent_age": "42",
    "deponent_occupation": "Farmer", "deponent_address": "Village Ratnagiri, Dist. Ratnagiri",
    "notarisation_authority": "Shri Anil Kumar, Notary Public",
    "place_of_execution": "Ratnagiri",
    "execution_date": "12 March 2026",
    "notary_registration_number": "",          # intentionally blank to test fallback
    # leave_and_license
    "licensor_name": "Ramesh Patil", "licensor_address": "123 MG Road, Pune",
    "licensee_name": "Suresh Shah", "licensee_address": "456 FC Road, Pune",
    "commencement_date": "1 April 2026",
    "agreement_duration_months": "11",
    "monthly_license_fee": "42000", "security_deposit": "252000",
    "witness_1_name": "Harish Shah", "witness_2_name": "Kavita Jain",
    # conveyance_deed
    "conveyor_name": "ABC Developers Pvt Ltd",
    "conveyor_address": "Developer House, Thane 400601",
    "transferee_name": "XYZ Co-operative Housing Society",
    # transferee_address intentionally absent to test SOCIETY ADDRESS fallback
    # all deed types
    "registration_office": "Sub-Registrar, Mulshi, Pune",
    "seller_name": "Ramesh Patil", "seller_address": "123 MG Road, Pune",
    "buyer_name": "Suresh Shah", "buyer_address": "456 FC Road, Pune",
    "mortgagor_name": "Ramesh Patil", "mortgagor_address": "123 MG Road, Pune",
    "mortgagee_name": "SBI Bank", "mortgagee_address": "Main Branch, Pune",
    "principal_name": "Ramesh Patil", "principal_address": "123 MG Road, Pune",
    "attorney_name": "Suresh Shah", "attorney_address": "456 FC Road, Pune",
    "donor_name": "Ramesh Patil", "donor_address": "123 MG Road, Pune",
    "donee_name": "Suresh Shah", "donee_address": "456 FC Road, Pune",
}
FP = {"intake": FULL_INTAKE, "ocr_fields": {"Survey_Number": "123/4A"}}

DEED_TYPES = ["sale_deed", "mortgage_deed", "power_of_attorney",
              "leave_and_license", "gift_deed", "conveyance_deed", "affidavit"]

# ─────────────────────────────────────────────────────────────────────────────
# ISSUE 5: Affidavit notarisation block
# ─────────────────────────────────────────────────────────────────────────────
print("\n[Issue 5] Affidavit notarisation block")

aff_parties = _build_slot_prompt("PARTIES_CLAUSE", FP, "ctx", "affidavit", ENTRIES)
aff_attest  = _build_slot_prompt("ATTESTATION",    FP, "ctx", "affidavit", ENTRIES)
aff_recitals = _build_slot_prompt("RECITALS",      FP, "ctx", "affidavit", ENTRIES)

checks_5 = [
    ("PARTIES: 'on oath' in formula",
        "on oath" in aff_parties),
    ("PARTIES: 'do NOT write solemn affirmation' prohibition",
        "solemn affirmation" in aff_parties and "do NOT" in aff_parties),
    ("PARTIES: deponent_age field referenced",
        "deponent_age" in aff_parties),
    ("PARTIES: deponent_occupation field referenced",
        "deponent_occupation" in aff_parties),
    ("ATTESTATION: notarisation_authority field referenced",
        "notarisation_authority" in aff_attest),
    ("ATTESTATION: place_of_execution field referenced",
        "place_of_execution" in aff_attest),
    ("ATTESTATION: execution_date field referenced",
        "execution_date" in aff_attest),
    ("ATTESTATION: NOTARY REG. NO. fallback present",
        "NOTARY REG. NO." in aff_attest),
    ("ATTESTATION: [Insert Name] placeholder forbidden",
        "do NOT use generic placeholder" in aff_attest),
    ("RECITALS: is SEPARATE numbered statements section",
        "SEPARATE" in aff_recitals and "numbered paragraph" in aff_recitals),
    ("RECITALS: truth declaration present",
        "true to the best" in aff_recitals),
]

for label, result in checks_5:
    status = "PASS" if result else "FAIL"
    print(f"  {status}: {label}")
    if not result:
        errors.append(f"Issue 5 — {label}")

# ─────────────────────────────────────────────────────────────────────────────
# ISSUE 6: Sub-Registrar office in attestation for all deed types
# ─────────────────────────────────────────────────────────────────────────────
print("\n[Issue 6] Sub-Registrar office in ATTESTATION — all deed types")

DEED_TYPES_FOR_SUBREG = ["sale_deed", "mortgage_deed", "power_of_attorney",
                          "leave_and_license", "gift_deed", "conveyance_deed"]

for doc in DEED_TYPES_FOR_SUBREG:
    attest = _build_slot_prompt("ATTESTATION", FP, "ctx", doc, ENTRIES)
    has_subreg = (
        "Sub-Registrar" in attest or
        "registration_office" in attest or
        "sub-registrar" in attest.lower()
    )
    has_no_blank = (
        "do NOT leave" in attest or
        "do not leave" in attest.lower() or
        "registration_office" in attest
    )
    status = "PASS" if (has_subreg and has_no_blank) else "FAIL"
    print(f"  {status}: {doc} ATTESTATION references Sub-Registrar/registration_office")
    if not (has_subreg and has_no_blank):
        errors.append(f"Issue 6 — {doc}: Sub-Registrar not in ATTESTATION")

# Verify the default ATTESTATION fallback also includes registration_office
default_attest_prompt = _build_slot_prompt("ATTESTATION", FP, "ctx", "sale_deed", ENTRIES)
has_reg_office = "registration_office" in default_attest_prompt
print(f"  {'PASS' if has_reg_office else 'FAIL'}: default ATTESTATION fallback includes registration_office")
if not has_reg_office:
    errors.append("Issue 6 — default ATTESTATION fallback missing registration_office")

# ─────────────────────────────────────────────────────────────────────────────
# ISSUE 7: Leave and License execution date
# ─────────────────────────────────────────────────────────────────────────────
print("\n[Issue 7] Leave and License execution date")

ll_parties = _build_slot_prompt("PARTIES_CLAUSE", FP, "ctx", "leave_and_license", ENTRIES)
ll_attest  = _build_slot_prompt("ATTESTATION",    FP, "ctx", "leave_and_license", ENTRIES)

checks_7 = [
    ("PARTIES: commencement_date field referenced",
        "commencement_date" in ll_parties),
    ("PARTIES: FIRST LINE instruction present",
        "FIRST LINE" in ll_parties),
    ("PARTIES: [insert date] placeholder forbidden",
        "insert date" in ll_parties and ("do NOT" in ll_parties or "do not" in ll_parties.lower())),
    ("ATTESTATION: commencement_date for execution date",
        "commencement_date" in ll_attest),
    ("ATTESTATION: [insert date] placeholder forbidden",
        "insert date" in ll_attest and ("do NOT" in ll_attest or "do not" in ll_attest.lower())),
    ("ATTESTATION: witness_1_name and witness_2_name referenced",
        "witness_1_name" in ll_attest and "witness_2_name" in ll_attest),
    ("ATTESTATION: registration note for 11-month agreements",
        "11 months" in ll_attest or "optional" in ll_attest),
]

for label, result in checks_7:
    status = "PASS" if result else "FAIL"
    print(f"  {status}: {label}")
    if not result:
        errors.append(f"Issue 7 — {label}")

# ─────────────────────────────────────────────────────────────────────────────
# ISSUE 8: Conveyance Deed Society address
# ─────────────────────────────────────────────────────────────────────────────
print("\n[Issue 8] Conveyance Deed Society address")

conv_parties = _build_slot_prompt("PARTIES_CLAUSE", FP, "ctx", "conveyance_deed", ENTRIES)
conv_attest  = _build_slot_prompt("ATTESTATION",    FP, "ctx", "conveyance_deed", ENTRIES)
conv_sched   = _build_slot_prompt("SCHEDULE",       FP, "ctx", "conveyance_deed", ENTRIES)

checks_8 = [
    ("PARTIES: [Insert Address] placeholder forbidden",
        "Insert Address" in conv_parties and "do NOT" in conv_parties),
    ("PARTIES: SOCIETY ADDRESS fallback present",
        "SOCIETY ADDRESS" in conv_parties),
    ("PARTIES: transferee_address field referenced",
        "transferee_address" in conv_parties),
    ("ATTESTATION: Sub-Registrar with district example (Thane)",
        "Thane" in conv_attest),
    ("ATTESTATION: Sub-Registrar office not left blank",
        "do NOT leave the Sub-Registrar office blank" in conv_attest),
    ("SCHEDULE: flat/unit details (unit_number)",
        "unit_number" in conv_sched),
    ("SCHEDULE: carpet_area referenced",
        "carpet_area" in conv_sched),
    ("SCHEDULE: boundary description present",
        "BOUNDARY NORTH" in conv_sched),
]

for label, result in checks_8:
    status = "PASS" if result else "FAIL"
    print(f"  {status}: {label}")
    if not result:
        errors.append(f"Issue 8 — {label}")

# ─────────────────────────────────────────────────────────────────────────────
# End-to-end assembly: verify all 7 doc types produce complete output
# ─────────────────────────────────────────────────────────────────────────────
print("\n[E2E] _assemble_from_slots — all 7 doc types, section_completeness=1.0")
from src.generation.template_mapper import TemplateRegistry, DEFAULT_SCHEMA_SLOTS
import logging; logging.disable(logging.WARNING)
registry = TemplateRegistry()
EXPECTED = ["PARTIES", "RECITALS", "OPERATIVE CLAUSE", "SCHEDULE", "ATTESTATION"]

for doc in DEED_TYPES:
    try:
        tmpl = registry.get_template(doc)
        slots = [s.name for s in sorted(tmpl.slots, key=lambda s: s.position)]
    except Exception:
        slots = list(DEFAULT_SCHEMA_SLOTS)

    slot_contents = {s: f"Content for {s} in {doc}." for s in slots}
    body = _assemble_from_slots(slot_contents, slots, doc, ENTRIES)
    present = sum(1 for h in EXPECTED
                  if re.search(rf"(?:^|\n){re.escape(h)}", body, re.IGNORECASE))
    sc = present / len(EXPECTED)
    status = "PASS" if sc == 1.0 else "FAIL"
    print(f"  {status}: {doc} section_completeness={sc:.1f}")
    if sc < 1.0:
        missing = [h for h in EXPECTED
                   if not re.search(rf"(?:^|\n){re.escape(h)}", body, re.IGNORECASE)]
        errors.append(f"E2E {doc}: missing headings {missing}")

logging.disable(logging.NOTSET)

# ─────────────────────────────────────────────────────────────────────────────
# Backend API import check
# ─────────────────────────────────────────────────────────────────────────────
print("\n[Backend] API import check")
try:
    import backend.main  # noqa: F401
    print("  PASS: backend.main imports without errors")
except Exception as e:
    errors.append(f"backend.main: {e}")
    print(f"  FAIL: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 65)
if errors:
    print(f"RESULT: {len(errors)} FAILURE(S)")
    for e in errors:
        print(f"  - {e}")
else:
    print("RESULT: ALL CHECKS PASSED — issues 5-8 fully fixed and integrated")
print("=" * 65)
