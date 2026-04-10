"""Test _assemble_from_slots produces all required headings for section_completeness=1.0."""
import re, sys
sys.path.insert(0, ".")
from src.cag.engine import _assemble_from_slots

class E:
    def __init__(self, name):
        self.name = name; self.content = "content"; self.tokens = 100; self.priority = "high"

entries = [E("Transfer of Property Act, 1882"), E("Maharashtra Stamp Act, 1958")]
SLOTS = ["PARTIES_CLAUSE","RECITALS","OPERATIVE_CLAUSE_1","OPERATIVE_CLAUSE_2","SCHEDULE","ATTESTATION"]
DOC_TYPES = ["sale_deed","mortgage_deed","power_of_attorney","leave_and_license","gift_deed","conveyance_deed","affidavit"]
EXPECTED = ["PARTIES", "RECITALS", "OPERATIVE CLAUSE", "SCHEDULE", "ATTESTATION"]

print("=== _assemble_from_slots: section_completeness simulation ===")
errors = []
for doc in DOC_TYPES:
    slot_contents = {s: f"Content for {s} in {doc}." for s in SLOTS}
    body = _assemble_from_slots(slot_contents, SLOTS, doc, entries)
    present = sum(1 for h in EXPECTED if re.search(rf"(?:^|\n){re.escape(h)}", body, re.IGNORECASE))
    sc = present / len(EXPECTED)
    status = "OK  " if sc == 1.0 else "FAIL"
    print(f"  {status}: {doc} section_completeness={sc:.1f} ({present}/{len(EXPECTED)})")
    if sc < 1.0:
        missing = [h for h in EXPECTED if not re.search(rf"(?:^|\n){re.escape(h)}", body, re.IGNORECASE)]
        errors.append(f"{doc}: missing {missing}")

print()
if errors:
    print("FAILURES:")
    for e in errors: print(f"  {e}")
else:
    print("ALL PASSED — section_completeness=1.0 achievable for all 7 doc types")
