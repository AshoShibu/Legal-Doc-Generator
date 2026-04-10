"""
Cache manifest coverage tests (Task 18.3).

For each document type, asserts that the LLM's most common citation patterns
are present in the manifest — preventing silent cache misses where the LLM
cites an act that was never loaded into the context window.

Each document type has a set of REQUIRED_ACTS: the acts that a practising
Maharashtra advocate would expect to see cited in that document type. If any
required act is absent from the manifest, the test fails with a clear message
identifying the gap.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Required citation patterns per document type.
# Keys are document type strings matching the manifest file names.
# Values are lists of (act_name_fragment, reason) tuples — the fragment is
# matched case-insensitively against manifest entry names.
# ---------------------------------------------------------------------------
_REQUIRED_ACTS: dict[str, list[tuple[str, str]]] = {
    "sale_deed": [
        ("Transfer of Property Act", "TPA s.54 governs sale of immovable property"),
        ("Registration Act", "Registration Act s.17 mandates compulsory registration"),
        ("Maharashtra Stamp Act", "Stamp Act Article 25 sets stamp duty on sale deeds"),
        ("Maharashtra Land Revenue Code", "MLRC s.149-153 governs mutation after sale"),
    ],
    "mortgage_deed": [
        ("Transfer of Property Act", "TPA s.58 defines mortgage types (simple/equitable/English)"),
        ("Registration Act", "Registration Act s.17 mandates registration of mortgages > Rs 100"),
        ("SARFAESI Act", "SARFAESI s.13 governs enforcement of security interest"),
        ("Maharashtra Stamp Act", "Stamp Act Article 6 sets stamp duty on mortgage deeds"),
    ],
    "power_of_attorney": [
        ("Powers of Attorney Act", "Powers of Attorney Act s.1-4 governs POA execution"),
        ("Indian Contract Act", "Contract Act s.182-238 governs agency and authority"),
        ("Registration Act", "Registration Act s.32 governs presentation by agent"),
        ("Indian Stamp Act", "Stamp Act Article 48 sets stamp duty on POA"),
    ],
    "leave_and_license": [
        ("Maharashtra Rent Control Act", "MRCA s.24 governs license period and termination"),
        ("Registration Act", "Registration Act s.17(1)(d) mandates registration of L&L > 12 months"),
        ("Transfer of Property Act", "TPA s.105-117 distinguishes lease from license"),
        ("Maharashtra Stamp Act", "Stamp Act Article 36A sets stamp duty on L&L agreements"),
    ],
    "gift_deed": [
        ("Transfer of Property Act", "TPA s.122-129 governs gift of immovable property"),
        ("Registration Act", "Registration Act s.17 mandates registration of gift deeds"),
        ("Maharashtra Stamp Act", "Stamp Act Article 28 sets stamp duty on gift deeds"),
    ],
    "conveyance_deed": [
        ("Transfer of Property Act", "TPA s.54-57 governs conveyance of immovable property"),
        ("Registration Act", "Registration Act s.17 mandates registration"),
        ("Maharashtra Ownership Flats Act", "MOFA s.11 governs deemed conveyance to society"),
        ("Real Estate", "RERA s.11 governs developer obligations and OC status"),
        ("Maharashtra Stamp Act", "Stamp Act Article 25 sets stamp duty on conveyance"),
    ],
    "affidavit": [
        ("Code of Civil Procedure", "CPC Order XIX governs affidavit admissibility"),
        ("Oaths Act", "Oaths Act s.3-5 governs administration of oath to deponent"),
        ("Indian Evidence Act", "Evidence Act s.59-60 governs proof by affidavit"),
        ("Notaries Act", "Notaries Act s.8 governs notarisation of affidavits"),
    ],
}

_MANIFEST_DIR = Path("config/cache_manifests")


def _load_manifest(doc_type: str) -> list[str]:
    """Return list of entry names from the YAML manifest for doc_type."""
    manifest_path = _MANIFEST_DIR / f"{doc_type}.yaml"
    if not manifest_path.exists():
        pytest.fail(f"Manifest file not found: {manifest_path}")
    with manifest_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    entries = data.get("priority_order", [])
    return [e.get("name", "") for e in entries if isinstance(e, dict)]


def _act_present(act_fragment: str, entry_names: list[str]) -> bool:
    """Return True if act_fragment appears (case-insensitive) in any entry name."""
    fragment_lower = act_fragment.lower()
    return any(fragment_lower in name.lower() for name in entry_names)


# ---------------------------------------------------------------------------
# Parametrised test — one test case per (doc_type, required_act) pair
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "doc_type,act_fragment,reason",
    [
        (doc_type, act_fragment, reason)
        for doc_type, required in _REQUIRED_ACTS.items()
        for act_fragment, reason in required
    ],
    ids=lambda x: x if isinstance(x, str) else "",
)
def test_manifest_contains_required_act(
    doc_type: str, act_fragment: str, reason: str
) -> None:
    """
    Assert that the cache manifest for doc_type contains an entry whose name
    includes act_fragment. This prevents silent cache misses where the LLM
    cites an act that was never loaded into the context window.
    """
    entry_names = _load_manifest(doc_type)
    assert _act_present(act_fragment, entry_names), (
        f"[{doc_type}] Required act '{act_fragment}' is MISSING from the manifest.\n"
        f"  Reason: {reason}\n"
        f"  Current manifest entries:\n"
        + "\n".join(f"    - {n}" for n in entry_names)
    )


# ---------------------------------------------------------------------------
# Structural integrity tests — every manifest must be well-formed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("doc_type", list(_REQUIRED_ACTS.keys()))
def test_manifest_structure(doc_type: str) -> None:
    """Every manifest must have a document_type field and a non-empty priority_order."""
    manifest_path = _MANIFEST_DIR / f"{doc_type}.yaml"
    assert manifest_path.exists(), f"Manifest file missing: {manifest_path}"

    with manifest_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    assert data.get("document_type") == doc_type, (
        f"Manifest document_type mismatch: expected '{doc_type}', "
        f"got '{data.get('document_type')}'"
    )
    entries = data.get("priority_order", [])
    assert len(entries) >= 3, (
        f"Manifest for '{doc_type}' has only {len(entries)} entries — "
        "expected at least 3 (core acts + 1 BHC judgment)"
    )
    for i, entry in enumerate(entries):
        assert "name" in entry, f"Entry {i} in '{doc_type}' manifest is missing 'name'"
        assert "path" in entry, f"Entry {i} in '{doc_type}' manifest is missing 'path'"
        assert "tokens" in entry, f"Entry {i} in '{doc_type}' manifest is missing 'tokens'"
        assert "section_hint" in entry, (
            f"Entry {i} ('{entry.get('name', '?')}') in '{doc_type}' manifest "
            "is missing 'section_hint' — add a section_hint to reduce context noise"
        )


@pytest.mark.parametrize("doc_type", list(_REQUIRED_ACTS.keys()))
def test_manifest_canonical_act_name_format(doc_type: str) -> None:
    """
    Act names must follow the canonical format: 'Act Name, Year'
    (comma before year). This ensures citation format compliance downstream.
    """
    entry_names = _load_manifest(doc_type)
    _ACT_NAME_RE = re.compile(r".+,\s*\d{4}$")
    # Only check entries that look like acts (not BHC judgments)
    act_entries = [
        n for n in entry_names
        if "judgment" not in n.lower() and "bhc" not in n.lower()
    ]
    for name in act_entries:
        assert _ACT_NAME_RE.match(name), (
            f"[{doc_type}] Act name '{name}' does not follow canonical format "
            "'Act Name, Year' (comma before year). "
            "This will cause citation format compliance failures."
        )
