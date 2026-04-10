"""Audit engine.py for corruption and verify all fixes are present."""
import ast, re, sys

path = "src/cag/engine.py"
content = open(path, encoding="utf-8").read()

# ── Syntax check ─────────────────────────────────────────────────────────────
try:
    ast.parse(content)
    print("SYNTAX OK")
except SyntaxError as e:
    print(f"SYNTAX ERROR line {e.lineno}: {e.msg}")
    sys.exit(1)

# ── Locate _build_slot_prompt ─────────────────────────────────────────────────
funcs = [(m.start(), m.group(1)) for m in re.finditer(r"^def (\w+)", content, re.MULTILINE)]
func_map = {name: pos for pos, name in funcs}

bsp_start = func_map["_build_slot_prompt"]
bsp_end   = func_map["_normalise_citation_courts"]
func_body = content[bsp_start:bsp_end]
print(f"\n_build_slot_prompt: chars {bsp_start}–{bsp_end} ({len(func_body)} chars)")

# ── Corruption checks ─────────────────────────────────────────────────────────
corruption = [
    ("duplicate sale_deed operative entry",
        func_body.count('"sale_deed": (\n            "a substantive sale clause') > 1),
    ("broken elif RECITALS (no body)",
        'elif slot_name == "RECITALS":\n    elif' in func_body),
    ("return bodytries corruption",
        "return bodytries" in func_body),
    ("needs_court inside _build_slot_prompt",
        "needs_court = court_token" in func_body),
    ("orphaned mortgage_deed recital fragment",
        '"),  "(2) the loan amount' in func_body),
    ("duplicate ATTESTATION elif block",
        func_body.count('elif slot_name == "ATTESTATION":') > 1),
    ("duplicate SCHEDULE elif block",
        func_body.count('elif slot_name == "SCHEDULE":') > 1),
]

print("\nCorruption checks:")
any_corrupt = False
for label, is_corrupt in corruption:
    status = "CORRUPT" if is_corrupt else "OK"
    print(f"  {status}: {label}")
    if is_corrupt:
        any_corrupt = True

# ── Fix presence checks ───────────────────────────────────────────────────────
fixes = [
    # Fix 1: Slot fill rate — all doc types have specific descriptions
    ("sale_deed PARTIES has Vendor/Purchaser",
        '"sale_deed": (\n            "the opening clause identifying the Vendor' in func_body),
    ("mortgage_deed PARTIES has Mortgagor/Mortgagee",
        '"mortgage_deed": (\n            "the opening clause identifying the Mortgagor' in func_body),
    ("power_of_attorney PARTIES has General/Special POA",
        "General Power of Attorney" in func_body),
    ("leave_and_license PARTIES has commencement_date",
        "commencement_date" in func_body),
    ("gift_deed PARTIES has Donor/Donee",
        '"gift_deed": (\n            "the opening clause identifying the Donor' in func_body),
    ("conveyance_deed PARTIES has SOCIETY ADDRESS fallback",
        "SOCIETY ADDRESS" in func_body),
    ("affidavit PARTIES has on oath formula",
        "on oath" in func_body),

    # Fix 2: POA — RECITALS, revocation, attestation
    ("POA RECITALS has principal ownership",
        "principal's ownership of or interest in the property" in func_body),
    ("POA OPERATIVE has revocation clause",
        "revocation clause" in func_body),
    ("POA ATTESTATION has dedicated entry",
        '"power_of_attorney": (\n            "the execution and attestation block' in func_body),

    # Fix 3: Boundary descriptions
    ("SCHEDULE has BOUNDARY NORTH MANUAL ENTRY",
        "BOUNDARY NORTH" in func_body),
    ("SCHEDULE forbids Shri X placeholder",
        "Shri X" in func_body),  # present as prohibition
    ("leave_and_license SCHEDULE is premises-based",
        "schedule of licensed premises" in func_body),
    ("conveyance_deed SCHEDULE has flat/unit details",
        "unit_number" in func_body),

    # Fix 4: Maharashtra Stamp Act Article 25
    ("sale_deed OPERATIVE has Stamp Act Article 25",
        "Maharashtra Stamp Act, 1958, Article 25" in func_body),
    ("conveyance_deed OPERATIVE has MahaRERA",
        "MahaRERA registration number" in func_body),

    # Affidavit fixes
    ("affidavit ATTESTATION has notarisation_authority",
        "notarisation_authority" in func_body),
    ("affidavit ATTESTATION has NOTARY REG. NO. fallback",
        "NOTARY REG. NO." in func_body),
    ("affidavit RECITALS is SEPARATE numbered statements",
        "SEPARATE" in func_body and "numbered paragraph" in func_body),

    # gift_deed fixes
    ("gift_deed ATTESTATION has registration_office",
        '"gift_deed": (\n            "the execution and attestation block' in func_body),
    ("gift_deed OPERATIVE prohibits duplicate ack blocks",
        "do NOT include a separate ACKNOWLEDGMENT" in func_body),

    # leave_and_license attestation
    ("leave_and_license ATTESTATION has registration note",
        "registration is optional" in func_body),

    # mortgage_deed RECITALS has TPA 58 distinction
    ("mortgage_deed RECITALS has TPA Section 58(b)",
        "TPA Section 58(b)" in func_body),
]

print("\nFix presence checks:")
any_missing = False
for label, present in fixes:
    status = "PRESENT" if present else "MISSING"
    print(f"  {status}: {label}")
    if not present:
        any_missing = True

print()
if any_corrupt:
    print("RESULT: CORRUPTION DETECTED — rewrite needed")
elif any_missing:
    print("RESULT: SOME FIXES MISSING — partial apply needed")
else:
    print("RESULT: ALL CHECKS PASSED — engine.py is clean and complete")
