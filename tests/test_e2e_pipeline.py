"""
tests/test_e2e_pipeline.py — End-to-end pipeline integration test.

Verifies the full chain:
  OCR → PII Redactor → CAG Engine (stubbed LLM) → Document Generator → Export

Uses a sample mortgage PDF from 'Maharashtra Legal Document Dataset/Mortage and land deed/'.

This test stubs the Ollama LLM call so it works without a running Ollama server.
"""
from __future__ import annotations

import hashlib
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_PDF = Path(
    "Maharashtra Legal Document Dataset/Mortage and land deed/mortgage_1.pdf"
)

STUB_LLM_RESPONSE = """\
PARTIES
This Mortgage Deed is made between Party A (Mortgagor) and Party B (Mortgagee).

RECITALS
Whereas the Mortgagor is the absolute owner of the property described in the Schedule.
This deed is governed by [Transfer of Property Act, 1882] Section 58, [Legislature], [1882].

OPERATIVE CLAUSE 1
The Mortgagor hereby mortgages the property to the Mortgagee as security for the loan.
As per [Registration Act, 1908] Section 17, [Legislature], [1908], this deed requires registration.

SCHEDULE
Survey Number: 123/4A, Village: Pune, Taluka: Haveli, District: Pune.

IN WITNESS WHEREOF
The parties have signed this deed on the date mentioned above.
"""


def _stub_ollama(model_tag: str, prompt: str, timeout: int = 180) -> str:
    """Return a deterministic stub response instead of calling Ollama."""
    return STUB_LLM_RESPONSE


# ---------------------------------------------------------------------------
# End-to-end test
# ---------------------------------------------------------------------------


def test_e2e_pipeline_ocr_to_export(tmp_path):
    """
    Full pipeline: OCR → PII → CAG (stubbed LLM) → Document Generator → Export.

    Verifies:
    - OCR returns an OCRResult (even if fields are sparse for a scanned PDF)
    - PII redactor processes the text without crashing
    - CAG engine loads the mortgage_deed cache and generates a draft
    - Document generator assembles a GeneratedDocument with header + citation index
    - Exporter writes DOCX and PDF files to the output directory
    - Run logger records the run and it is retrievable by run_id
    """
    assert SAMPLE_PDF.exists(), (
        f"Sample PDF not found at {SAMPLE_PDF}. "
        "Ensure 'Maharashtra Legal Document Dataset/Mortage and land deed/' is present."
    )

    # ------------------------------------------------------------------ #
    # Step 1: OCR                                                          #
    # ------------------------------------------------------------------ #
    from src.ocr.pipeline import extract, OCRResult

    ocr_result: OCRResult = extract(str(SAMPLE_PDF))

    assert isinstance(ocr_result, OCRResult), "OCR must return an OCRResult"
    assert ocr_result.source_ref == str(SAMPLE_PDF)
    # Fields may be empty for a scanned PDF without Ollama, but no crash
    assert isinstance(ocr_result.fields, dict)
    assert isinstance(ocr_result.errors, list)
    assert isinstance(ocr_result.warnings, list)

    print(f"\n[OCR] fields extracted: {list(ocr_result.fields.keys())}")
    print(f"[OCR] errors: {ocr_result.errors}")
    print(f"[OCR] warnings: {ocr_result.warnings}")

    # ------------------------------------------------------------------ #
    # Step 2: PII Redaction                                                #
    # ------------------------------------------------------------------ #
    from src.pii.redactor import redact, RedactionResult

    # Build a representative text from OCR fields + some synthetic PII
    ocr_text = "\n".join(
        f"{k}: {v}" for k, v in ocr_result.fields.items()
    )
    # Add synthetic PII to verify redaction works
    test_text = (
        ocr_text
        + "\nAadhaar: 1234 5678 9012\n"
        + "PAN: ABCDE1234F\n"
        + "Mobile: 9876543210\n"
        + "Email: test@example.com\n"
    )

    pii_result: RedactionResult = redact(test_text, ocr_result.fields)

    assert isinstance(pii_result, RedactionResult)
    assert "<AADHAAR_REDACTED>" in pii_result.redacted_text, "Aadhaar not redacted"
    assert "<PAN_REDACTED>" in pii_result.redacted_text, "PAN not redacted"
    assert "<MOBILE_REDACTED>" in pii_result.redacted_text, "Mobile not redacted"
    assert "<EMAIL_REDACTED>" in pii_result.redacted_text, "Email not redacted"
    assert isinstance(pii_result.audit_log, list)

    print(f"[PII] redacted {len(pii_result.audit_log)} entities")

    # ------------------------------------------------------------------ #
    # Step 3: CAG — load cache (stub LLM to avoid Ollama dependency)      #
    # ------------------------------------------------------------------ #
    from src.cag.engine import load_cache, generate_draft, LegalCache, DraftResult

    with patch("src.cag.engine._call_ollama", side_effect=_stub_ollama):
        cache: LegalCache = load_cache("mortgage_deed", "qwen2.5_1.5b")

        assert isinstance(cache, LegalCache)
        assert cache.document_type == "mortgage_deed"
        assert cache.llm_backend == "qwen2.5_1.5b"
        assert isinstance(cache.documents, list)
        assert len(cache.documents) > 0, "Cache must contain at least one document"
        assert cache.total_tokens <= int(32768 * 0.90) + 1, (
            "Total tokens must not exceed 90% of context window"
        )

        print(f"[CAG] loaded {len(cache.documents)} documents, {cache.total_tokens} tokens")
        print(f"[CAG] omitted: {cache.omitted_documents}")

        # Generate draft
        fact_pattern = {
            "mortgagor": "Party A",
            "mortgagee": "Party B",
            "property": "Survey No. 123/4A, Pune",
            "loan_amount": "500000",
            "interest_rate": "12%",
        }

        draft: DraftResult = generate_draft(fact_pattern, cache, "mortgage_deed")

        assert isinstance(draft, DraftResult)
        assert isinstance(draft.content, str)
        assert len(draft.content) > 0, "Draft content must not be empty"
        assert draft.session_id == cache.session_id

        print(f"[CAG] draft length: {len(draft.content)} chars")
        print(f"[CAG] citations: {len(draft.citations)}, ungrounded: {len(draft.ungrounded_clauses)}")

    # ------------------------------------------------------------------ #
    # Step 4: Document Generator                                           #
    # ------------------------------------------------------------------ #
    from src.generation.document_generator import generate_document, GeneratedDocument

    run_id = str(uuid.uuid4())

    generated: GeneratedDocument = generate_document(
        llm_output=draft.content,
        template=None,
        cache_or_chunks=cache,
        doc_type="mortgage_deed",
        pipeline_variant="CAG",
        llm_model="llama3:8b",
        run_id=run_id,
        cache_version="1.0.0",
    )

    assert isinstance(generated, GeneratedDocument)
    assert generated.run_id == run_id
    assert "GENERATED BY" in generated.header, "Header must contain GENERATED BY"
    assert "CAG" in generated.header, "Header must contain pipeline variant"
    assert "llama3:8b" in generated.header, "Header must contain LLM model"
    assert run_id in generated.header, "Header must contain run_id"
    assert "AI-generated" in generated.header or "system-generated" in generated.header, "Header must contain disclaimer"
    assert "CITATION INDEX" in generated.content, "Document must contain citation index"

    print(f"[DocGen] document length: {len(generated.content)} chars")
    print(f"[DocGen] high_hallucination_risk: {generated.high_hallucination_risk}")
    print(f"[DocGen] grounded citations: {len(generated.citations)}")

    # ------------------------------------------------------------------ #
    # Step 5: Export (DOCX + PDF)                                          #
    # ------------------------------------------------------------------ #
    from src.generation.exporter import export_document, ExportResult

    output_dir = str(tmp_path / "output")
    export_result: ExportResult = export_document(
        document=generated,
        output_dir=output_dir,
        base_filename=run_id,
    )

    assert isinstance(export_result, ExportResult)

    if export_result.docx_path:
        assert Path(export_result.docx_path).exists(), "DOCX file must exist on disk"
        print(f"[Export] DOCX: {export_result.docx_path}")
    else:
        print(f"[Export] DOCX failed: {export_result.errors}")

    if export_result.pdf_path:
        assert Path(export_result.pdf_path).exists(), "PDF file must exist on disk"
        print(f"[Export] PDF: {export_result.pdf_path}")
    else:
        print(f"[Export] PDF failed: {export_result.errors}")

    # At least one export format must succeed
    assert export_result.docx_path or export_result.pdf_path, (
        f"Both DOCX and PDF export failed: {export_result.errors}"
    )

    # ------------------------------------------------------------------ #
    # Step 6: Run Logger                                                   #
    # ------------------------------------------------------------------ #
    import tempfile
    from src.logging.run_logger import log_run, get_run

    # Use a temp DB to avoid polluting the real output
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        tmp_db_path = tmp_db.name

    original_db = os.environ.get("LOG_DB_PATH")
    os.environ["LOG_DB_PATH"] = tmp_db_path

    try:
        # Reload the module-level _DB_PATH by patching
        import src.logging.run_logger as rl
        original_module_db = rl._DB_PATH
        rl._DB_PATH = tmp_db_path

        input_hash = hashlib.sha256(str(SAMPLE_PDF).encode()).hexdigest()
        output_hash = hashlib.sha256(generated.content.encode()).hexdigest()

        logged_run_id = log_run(
            pipeline_variant="cag",
            llm_model="mixtral_8x7b",
            input_hash=input_hash,
            cache_composition={"document_type": "mortgage_deed", "documents": len(cache.documents)},
            output_hash=output_hash,
            output_path=output_dir,
            status="success",
        )

        assert isinstance(logged_run_id, str)
        assert len(logged_run_id) > 0

        retrieved = get_run(logged_run_id)
        assert retrieved is not None, "get_run must return the logged run"
        assert retrieved["run_id"] == logged_run_id
        assert retrieved["pipeline_variant"] == "cag"
        assert retrieved["status"] == "success"

        print(f"[Logger] run_id: {logged_run_id}")
        print(f"[Logger] retrieved: pipeline_variant={retrieved['pipeline_variant']}, status={retrieved['status']}")

    finally:
        rl._DB_PATH = original_module_db
        if original_db is not None:
            os.environ["LOG_DB_PATH"] = original_db
        else:
            os.environ.pop("LOG_DB_PATH", None)
        # On Windows the SQLite file may still be held; best-effort cleanup
        try:
            Path(tmp_db_path).unlink(missing_ok=True)
        except PermissionError:
            pass  # Windows file lock — temp file will be cleaned up by OS

    print("\nPASSED: End-to-end pipeline test: OCR -> PII -> CAG -> DocGen -> Export -> Logger")
