"""
tests/test_multi_doc_output.py — Multi-document output persistence test.

Verifies that the full pipeline (OCR → PII → CAG → DocGen → Export → RunLog)
produces DOCX and PDF files on disk at the correct path for multiple document
types, exactly as the FastAPI backend expects.

Stubs the Ollama LLM call so no running server is needed.
Mirrors the backend generation flow closely enough to validate output persistence.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Test matrix — (doc_type, sample_pdf)
# ---------------------------------------------------------------------------
CORPUS = Path("Maharashtra Legal Document Dataset/Mortage and land deed")

TEST_CASES = [
    ("sale_deed",        CORPUS / "sale_deed_001.pdf"),
    ("mortgage_deed",    CORPUS / "mortgage_1.pdf"),
    ("gift_deed",        CORPUS / "mortgage_2.pdf"),
    ("affidavit",        CORPUS / "mortgage_3.pdf"),
    ("conveyance_deed",  CORPUS / "mortgage_4.pdf"),
]

# ---------------------------------------------------------------------------
# Stub LLM response — realistic enough to exercise citation extraction
# ---------------------------------------------------------------------------

def _stub_llm(doc_type: str) -> str:
    label = doc_type.replace("_", " ").title()
    return f"""\
{label.upper()}

This {label} is made between <PETITIONER_1> (Party A) and <RESPONDENT_1> (Party B).

RECITALS
Whereas Party A is the absolute owner of the property described in the Schedule below.
This deed is governed by [Transfer of Property Act, 1882] Section 54, [Legislature], [1882].

OPERATIVE CLAUSE 1
Party A hereby transfers the property to Party B for the consideration stated herein.
As per [Registration Act, 1908] Section 17, [Legislature], [1908], this deed requires
compulsory registration before the Sub-Registrar.

OPERATIVE CLAUSE 2
The property is free from all encumbrances as per [MLRC 1966] Section 32, [Legislature], [1966].

SCHEDULE
Survey Number: 123/4A, Village: Pune, Taluka: Haveli, District: Pune.
Area: 500 sq. metres.

IN WITNESS WHEREOF
The parties have executed this deed on the date mentioned above.
"""


# ---------------------------------------------------------------------------
# Helper — mirrors the backend generation flow
# ---------------------------------------------------------------------------

def run_pipeline_to_disk(
    pdf_path: Path,
    doc_type: str,
    output_root: Path,
    llm_backend: str = "qwen2.5_1.5b",
) -> dict:
    """
    Run the full CAG pipeline and write output to output_root.
    Returns a dict with keys: run_id, docx_path, pdf_path, log_entry.
    """
    import hashlib as _hashlib

    file_bytes = pdf_path.read_bytes()
    run_id = str(uuid.uuid4())
    pipeline_slug = "cag"

    # ---- OCR ----
    from src.ocr.pipeline import extract
    with tempfile.NamedTemporaryFile(suffix=pdf_path.suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    try:
        ocr_result = extract(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    # ---- PII Redaction ----
    from src.pii.redactor import redact
    raw_text = json.dumps(ocr_result.fields, ensure_ascii=False)
    redaction_result = redact(raw_text, ocr_result.fields)

    # ---- CAG: load cache + generate draft (LLM stubbed) ----
    from src.cag.engine import load_cache, generate_draft

    stub_response = _stub_llm(doc_type)
    with patch("src.cag.engine._call_ollama", return_value=stub_response):
        cache = load_cache(doc_type, llm_backend)
        fact_pattern = redaction_result.redacted_text or raw_text
        draft_result = generate_draft(fact_pattern, cache, doc_type)

    # ---- Document Generator ----
    from src.generation.document_generator import generate_document
    generated = generate_document(
        llm_output=draft_result.content,
        template=None,
        cache_or_chunks=cache,
        doc_type=doc_type,
        pipeline_variant="CAG",
        llm_model=llm_backend,
        run_id=run_id,
        cache_version=f"session:{cache.session_id[:8]}",
    )

    # ---- Export to persistent directory ----
    from src.generation.exporter import export_document
    persistent_dir = output_root / pipeline_slug / doc_type / run_id
    persistent_dir.mkdir(parents=True, exist_ok=True)
    export_result = export_document(generated, str(persistent_dir), run_id)

    # ---- Run Logger ----
    import src.logging.run_logger as rl
    original_db = rl._DB_PATH
    db_path = output_root / "runs.db"
    rl._DB_PATH = str(db_path)
    try:
        cache_comp = [{"name": d.name, "tokens": d.tokens} for d in cache.documents]
        docx_bytes = Path(export_result.docx_path).read_bytes() if export_result.docx_path and Path(export_result.docx_path).exists() else b""
        pdf_bytes  = Path(export_result.pdf_path).read_bytes()  if export_result.pdf_path  and Path(export_result.pdf_path).exists()  else b""
        output_hash = hashlib.sha256(docx_bytes or pdf_bytes).hexdigest()

        logged_id = rl.log_run(
            pipeline_variant=pipeline_slug,
            llm_model=llm_backend,
            input_hash=hashlib.sha256(file_bytes).hexdigest(),
            cache_composition=cache_comp,
            output_hash=output_hash,
            output_path=str(persistent_dir),
            status="success",
        )
        log_entry = rl.get_run(logged_id)
    finally:
        rl._DB_PATH = original_db

    return {
        "run_id": run_id,
        "docx_path": export_result.docx_path,
        "pdf_path": export_result.pdf_path,
        "export_errors": export_result.errors,
        "log_entry": log_entry,
        "generated": generated,
        "cache": cache,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("doc_type,pdf_path", TEST_CASES)
def test_pipeline_produces_files_on_disk(doc_type, pdf_path, tmp_path):
    """
    Full pipeline for each document type must:
    1. Complete without raising an exception
    2. Write a DOCX file to output/{pipeline}/{doc_type}/{run_id}/
    3. Write a PDF file to the same directory
    4. Record a retrievable run log entry with the correct output_path
    5. Generated document must contain the header, disclaimer, and citation index
    """
    assert pdf_path.exists(), f"Sample PDF missing: {pdf_path}"

    result = run_pipeline_to_disk(pdf_path, doc_type, tmp_path)

    run_id      = result["run_id"]
    docx_path   = result["docx_path"]
    pdf_path_out = result["pdf_path"]
    log_entry   = result["log_entry"]
    generated   = result["generated"]
    cache       = result["cache"]

    # ---- 1. DOCX exists on disk ----
    assert docx_path is not None, f"[{doc_type}] DOCX path is None. Errors: {result['export_errors']}"
    assert Path(docx_path).exists(), f"[{doc_type}] DOCX not found at {docx_path}"
    assert Path(docx_path).stat().st_size > 0, f"[{doc_type}] DOCX is empty"

    # ---- 2. PDF exists on disk ----
    assert pdf_path_out is not None, f"[{doc_type}] PDF path is None. Errors: {result['export_errors']}"
    assert Path(pdf_path_out).exists(), f"[{doc_type}] PDF not found at {pdf_path_out}"
    assert Path(pdf_path_out).stat().st_size > 0, f"[{doc_type}] PDF is empty"

    # ---- 3. Files are in the correct directory structure ----
    expected_dir = tmp_path / "cag" / doc_type / run_id
    assert Path(docx_path).parent == expected_dir, (
        f"[{doc_type}] DOCX in wrong dir: {Path(docx_path).parent} (expected {expected_dir})"
    )
    assert Path(pdf_path_out).parent == expected_dir, (
        f"[{doc_type}] PDF in wrong dir: {Path(pdf_path_out).parent} (expected {expected_dir})"
    )

    # ---- 4. Run log entry is retrievable and has correct output_path ----
    assert log_entry is not None, f"[{doc_type}] Run log entry not found"
    assert log_entry["status"] == "success", f"[{doc_type}] Run log status: {log_entry['status']}"
    assert log_entry["output_path"] == str(expected_dir), (
        f"[{doc_type}] Run log output_path mismatch: {log_entry['output_path']}"
    )
    assert log_entry["pipeline_variant"] == "cag"

    # ---- 5. Document content integrity ----
    assert "GENERATED BY" in generated.header, f"[{doc_type}] Missing GENERATED BY in header"
    assert "AI-generated" in generated.header or "system-generated" in generated.header, f"[{doc_type}] Missing disclaimer in header"
    assert run_id in generated.header, f"[{doc_type}] run_id missing from header"
    assert "CITATION INDEX" in generated.content, f"[{doc_type}] Missing citation index"

    # ---- 6. Cache loaded at least the two mandatory statutes ----
    cache_names = [d.name for d in cache.documents]
    assert len(cache_names) >= 2, f"[{doc_type}] Cache has fewer than 2 documents: {cache_names}"

    print(f"\nPASSED [{doc_type}]")
    print(f"   DOCX : {docx_path}  ({Path(docx_path).stat().st_size:,} bytes)")
    print(f"   PDF  : {pdf_path_out}  ({Path(pdf_path_out).stat().st_size:,} bytes)")
    print(f"   Cache: {len(cache_names)} docs, {cache.total_tokens} tokens")
    print(f"   Citations: {len(generated.citations)} grounded, "
          f"{len(generated.ungrounded_clauses)} ungrounded clauses")
    print(f"   Run ID: {run_id}")


def test_output_directory_structure(tmp_path):
    """
    After running all 5 document types, the output tree must have exactly
    5 subdirectories under output/cag/, one per doc_type, each containing
    exactly one run_id subdirectory with both .docx and .pdf files.
    """
    for doc_type, pdf_path in TEST_CASES:
        if not pdf_path.exists():
            pytest.skip(f"Sample PDF missing: {pdf_path}")
        run_pipeline_to_disk(pdf_path, doc_type, tmp_path)

    cag_dir = tmp_path / "cag"
    assert cag_dir.exists(), "output/cag/ directory was not created"

    doc_type_dirs = sorted(d.name for d in cag_dir.iterdir() if d.is_dir())
    expected_types = sorted(dt for dt, _ in TEST_CASES)
    assert doc_type_dirs == expected_types, (
        f"Expected doc_type dirs {expected_types}, got {doc_type_dirs}"
    )

    for doc_type in expected_types:
        type_dir = cag_dir / doc_type
        run_dirs = [d for d in type_dir.iterdir() if d.is_dir()]
        assert len(run_dirs) == 1, (
            f"Expected 1 run dir under {type_dir}, got {len(run_dirs)}"
        )
        run_dir = run_dirs[0]
        files = {f.suffix for f in run_dir.iterdir() if f.is_file()}
        assert ".docx" in files, f"[{doc_type}] No .docx in {run_dir}"
        assert ".pdf"  in files, f"[{doc_type}] No .pdf in {run_dir}"

    print(f"\nPASSED: Directory structure correct under {tmp_path / 'cag'}")
    for doc_type in expected_types:
        run_dir = next((cag_dir / doc_type).iterdir())
        for f in sorted(run_dir.iterdir()):
            print(f"   {f.relative_to(tmp_path)}  ({f.stat().st_size:,} bytes)")
