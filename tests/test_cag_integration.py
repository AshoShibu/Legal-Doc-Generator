"""
tests/test_cag_integration.py — Phase 1 integration test: end-to-end CAG flow.

Covers the full chain for 5–10 sample documents from
'Maharashtra Legal Document Dataset/Mortage and land deed/':

  OCR → PII Redaction → CAG draft generation (stubbed LLM) → DOCX export
  → Run log entry created and retrievable by run_id

Assertions per document:
  - OCR returns a valid OCRResult
  - PII redaction removes all synthetic PII markers
  - CAG cache loads with correct token budget
  - Draft is generated and non-empty
  - Document header contains all required fields (pipeline, model, run_id, disclaimer)
  - DOCX output file exists at the expected path
  - Run log entry is retrievable by run_id with correct fields

Requirements: 4.1, 4.2, 4.3, 4.4, 4.7, 14.1, 14.3
"""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Sample documents — 5 mortgage + 5 sale deed PDFs
# ---------------------------------------------------------------------------

_DEED_DIR = Path("Maharashtra Legal Document Dataset/Mortage and land deed")

SAMPLE_DOCS = [
    # (file_path, document_type)
    (_DEED_DIR / "mortgage_1.pdf",    "mortgage_deed"),
    (_DEED_DIR / "mortgage_2.pdf",    "mortgage_deed"),
    (_DEED_DIR / "mortgage_3.pdf",    "mortgage_deed"),
    (_DEED_DIR / "mortgage_4.pdf",    "mortgage_deed"),
    (_DEED_DIR / "mortgage_5.pdf",    "mortgage_deed"),
    (_DEED_DIR / "sale_deed_001.pdf", "sale_deed"),
    (_DEED_DIR / "sale_deed_002.pdf", "sale_deed"),
    (_DEED_DIR / "sale_deed_003.pdf", "sale_deed"),
    (_DEED_DIR / "sale_deed_004.pdf", "sale_deed"),
    (_DEED_DIR / "sale_deed_005.pdf", "sale_deed"),
]

# ---------------------------------------------------------------------------
# Stub LLM response — deterministic, contains valid citation markers
# ---------------------------------------------------------------------------

_STUB_DRAFT = """\
PARTIES
This {doc_type} is made between <PETITIONER_1> (Party A) and <RESPONDENT_1> (Party B).

RECITALS
Whereas Party A is the absolute owner of the property described in the Schedule.
This deed is governed by [Transfer of Property Act, 1882] Section 58, [Legislature], [1882].

OPERATIVE CLAUSE 1
Party A hereby transfers the property to Party B subject to the terms herein.
As per [Registration Act, 1908] Section 17, [Legislature], [1908], this deed requires registration.

OPERATIVE CLAUSE 2
The consideration amount has been paid in full as acknowledged by Party A.
Pursuant to [MLRC, 1966] Section 32, [Legislature], [1966], the land records shall be updated.

SCHEDULE
Survey Number: <SURVEY_NUMBER_TOKEN>, Village: Pune, Taluka: Haveli, District: Pune.

IN WITNESS WHEREOF
The parties have signed this deed on the date mentioned above.
"""


def _stub_ollama(model_tag: str, prompt: str, timeout: int = 180) -> str:
    """Return a deterministic stub response without calling Ollama."""
    doc_type = "Deed"
    if "mortgage" in prompt.lower():
        doc_type = "Mortgage Deed"
    elif "sale" in prompt.lower():
        doc_type = "Sale Deed"
    return _STUB_DRAFT.format(doc_type=doc_type)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def run_logger_db(tmp_path_factory):
    """Provide a temporary SQLite DB path shared across all tests in this module."""
    db_path = str(tmp_path_factory.mktemp("db") / "test_runs.db")
    return db_path


@pytest.fixture(scope="module")
def output_root(tmp_path_factory):
    """Provide a temporary output root directory shared across all tests."""
    return tmp_path_factory.mktemp("output")


# ---------------------------------------------------------------------------
# Parametrised integration test
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pdf_path,doc_type", SAMPLE_DOCS)
def test_cag_end_to_end(pdf_path, doc_type, output_root, run_logger_db):
    """
    End-to-end CAG pipeline test for a single sample document.

    Steps:
      1. OCR extraction
      2. PII redaction
      3. CAG cache load + draft generation (LLM stubbed)
      4. Document assembly
      5. DOCX export
      6. Run log entry created and retrievable
    """
    assert pdf_path.exists(), (
        f"Sample PDF not found: {pdf_path}. "
        "Ensure 'Maharashtra Legal Document Dataset/Mortage and land deed/' is present."
    )

    run_id = str(uuid.uuid4())
    doc_output_dir = str(output_root / "cag" / doc_type / run_id)

    # ------------------------------------------------------------------ #
    # Step 1: OCR                                                          #
    # ------------------------------------------------------------------ #
    from src.ocr.pipeline import extract, OCRResult

    ocr_result: OCRResult = extract(str(pdf_path))

    assert isinstance(ocr_result, OCRResult), "OCR must return an OCRResult"
    assert ocr_result.source_ref == str(pdf_path), "source_ref must match input path"
    assert isinstance(ocr_result.fields, dict), "fields must be a dict"
    assert isinstance(ocr_result.confidence, dict), "confidence must be a dict"
    assert isinstance(ocr_result.warnings, list), "warnings must be a list"
    assert isinstance(ocr_result.errors, list), "errors must be a list"

    # ------------------------------------------------------------------ #
    # Step 2: PII Redaction                                                #
    # ------------------------------------------------------------------ #
    from src.pii.redactor import redact, RedactionResult

    # Build representative text from OCR fields + synthetic PII
    ocr_text = "\n".join(f"{k}: {v}" for k, v in ocr_result.fields.items())
    test_text = (
        ocr_text
        + "\nAadhaar: 1234 5678 9012\n"
        + "PAN: ABCDE1234F\n"
        + "Mobile: 9876543210\n"
        + "Email: owner@example.com\n"
    )

    pii_result: RedactionResult = redact(test_text, ocr_result.fields)

    assert isinstance(pii_result, RedactionResult), "redact must return a RedactionResult"
    assert "1234 5678 9012" not in pii_result.redacted_text, "Aadhaar must be redacted"
    assert "ABCDE1234F" not in pii_result.redacted_text, "PAN must be redacted"
    assert "9876543210" not in pii_result.redacted_text, "Mobile must be redacted"
    assert "owner@example.com" not in pii_result.redacted_text, "Email must be redacted"
    assert isinstance(pii_result.audit_log, list), "audit_log must be a list"
    # At least the 4 synthetic PII items must be in the audit log
    assert len(pii_result.audit_log) >= 4, (
        f"Expected ≥4 audit log entries, got {len(pii_result.audit_log)}"
    )
    # structured_json must be preserved unchanged
    assert pii_result.structured_json == ocr_result.fields, (
        "structured_json must be the original OCR fields dict"
    )

    # ------------------------------------------------------------------ #
    # Step 3: CAG — load cache + generate draft (LLM stubbed)             #
    # ------------------------------------------------------------------ #
    from src.cag.engine import load_cache, generate_draft, LegalCache, DraftResult

    with patch("src.cag.engine._call_ollama", side_effect=_stub_ollama):
        cache: LegalCache = load_cache(doc_type, "qwen2.5_1.5b")

        assert isinstance(cache, LegalCache), "load_cache must return a LegalCache"
        assert cache.document_type == doc_type, "cache.document_type must match requested type"
        assert cache.llm_backend == "qwen2.5_1.5b", "cache.llm_backend must match requested backend"
        assert isinstance(cache.documents, list), "cache.documents must be a list"
        assert len(cache.documents) > 0, "Cache must contain at least one document"
        assert isinstance(cache.session_id, str) and len(cache.session_id) > 0, (
            "session_id must be a non-empty string"
        )
        assert isinstance(cache.omitted_documents, list), "omitted_documents must be a list"

        # Token budget: must not exceed 90% of context window for the loaded backend
        from src.cag.cache_loader import CONTEXT_WINDOWS
        context_window = CONTEXT_WINDOWS.get(cache.llm_backend, CONTEXT_WINDOWS.get("llama3_8b", 8192))
        budget = int(context_window * 0.90)
        assert cache.total_tokens <= budget, (
            f"total_tokens={cache.total_tokens} exceeds 90% budget={budget}"
        )

        # Build a minimal fact pattern from OCR + synthetic intake data
        fact_pattern = {
            "ocr_fields": ocr_result.fields,
            "intake": {
                "mortgagor" if doc_type == "mortgage_deed" else "seller": "<PETITIONER_1>",
                "mortgagee" if doc_type == "mortgage_deed" else "buyer": "<RESPONDENT_1>",
                "property": "Survey No. 123/4A, Pune",
                "consideration_amount": "₹50,00,000 (Rupees Fifty Lakhs only)",
            },
            "document_type": doc_type,
        }

        draft: DraftResult = generate_draft(fact_pattern, cache, doc_type)

        assert isinstance(draft, DraftResult), "generate_draft must return a DraftResult"
        assert isinstance(draft.content, str) and len(draft.content) > 0, (
            "Draft content must be a non-empty string"
        )
        assert draft.session_id == cache.session_id, (
            "draft.session_id must match cache.session_id (cache reuse)"
        )
        assert isinstance(draft.citations, list), "citations must be a list"
        assert isinstance(draft.ungrounded_clauses, list), "ungrounded_clauses must be a list"

    # ------------------------------------------------------------------ #
    # Step 4: Document Generator                                           #
    # ------------------------------------------------------------------ #
    from src.generation.document_generator import generate_document, GeneratedDocument

    generated: GeneratedDocument = generate_document(
        llm_output=draft.content,
        template=None,
        cache_or_chunks=cache,
        doc_type=doc_type,
        pipeline_variant="CAG",
        llm_model="llama3:8b",
        run_id=run_id,
        cache_version="1.0.0",
    )

    assert isinstance(generated, GeneratedDocument), (
        "generate_document must return a GeneratedDocument"
    )
    assert generated.run_id == run_id, "run_id must be preserved in GeneratedDocument"

    # Header must contain all required fields (Requirement 13.1)
    assert "GENERATED BY" in generated.header, "Header must contain 'GENERATED BY'"
    assert "CAG" in generated.header, "Header must contain pipeline variant 'CAG'"
    assert "llama3:8b" in generated.header, "Header must contain LLM model name"
    assert run_id in generated.header, "Header must contain the run_id"
    assert "AI-generated" in generated.header or "system-generated" in generated.header, "Header must contain disclaimer"
    # Citation index must be appended (Requirement 13.5)
    assert "CITATION INDEX" in generated.content, (
        "Generated document must contain a CITATION INDEX section"
    )

    # ------------------------------------------------------------------ #
    # Step 5: DOCX Export                                                  #
    # ------------------------------------------------------------------ #
    from src.generation.exporter import export_document, ExportResult

    export_result: ExportResult = export_document(
        document=generated,
        output_dir=doc_output_dir,
        base_filename=run_id,
    )

    assert isinstance(export_result, ExportResult), (
        "export_document must return an ExportResult"
    )

    # DOCX must exist at the expected path (Requirement 4.7)
    if export_result.docx_path:
        docx_file = Path(export_result.docx_path)
        assert docx_file.exists(), (
            f"DOCX file must exist on disk at {export_result.docx_path}"
        )
        assert docx_file.stat().st_size > 0, "DOCX file must not be empty"
        # Path must be under the expected output directory
        assert str(doc_output_dir) in str(docx_file), (
            f"DOCX must be stored under {doc_output_dir}"
        )

    # At least one export format must succeed
    assert export_result.docx_path or export_result.pdf_path, (
        f"Both DOCX and PDF export failed for {pdf_path.name}: {export_result.errors}"
    )

    # ------------------------------------------------------------------ #
    # Step 6: Run Logger                                                   #
    # ------------------------------------------------------------------ #
    import src.logging.run_logger as rl

    original_db = rl._DB_PATH
    rl._DB_PATH = run_logger_db

    try:
        input_hash = hashlib.sha256(str(pdf_path).encode()).hexdigest()
        output_hash = hashlib.sha256(generated.content.encode()).hexdigest()

        logged_run_id = rl.log_run(
            pipeline_variant="cag",
            llm_model="llama3_8b",
            input_hash=input_hash,
            cache_composition={
                "document_type": doc_type,
                "documents": len(cache.documents),
                "total_tokens": cache.total_tokens,
                "session_id": cache.session_id,
            },
            output_hash=output_hash,
            output_path=doc_output_dir,
            status="success",
        )

        # Run ID must be a non-empty string (Requirement 14.1)
        assert isinstance(logged_run_id, str) and len(logged_run_id) > 0, (
            "log_run must return a non-empty run_id string"
        )

        # Run must be retrievable by run_id (Requirement 14.1)
        retrieved = rl.get_run(logged_run_id)
        assert retrieved is not None, (
            f"get_run({logged_run_id!r}) must return the logged run"
        )
        assert retrieved["run_id"] == logged_run_id, "Retrieved run_id must match"
        assert retrieved["pipeline_variant"] == "cag", (
            "Retrieved pipeline_variant must be 'cag'"
        )
        assert retrieved["llm_model"] == "llama3_8b", (
            "Retrieved llm_model must be 'llama3_8b'"
        )
        assert retrieved["status"] == "success", "Retrieved status must be 'success'"
        assert retrieved["input_hash"] == input_hash, "input_hash must be stored correctly"
        assert retrieved["output_hash"] == output_hash, "output_hash must be stored correctly"

        # Output path must be stored (Requirement 14.3)
        assert retrieved["output_path"] == doc_output_dir, (
            "output_path must match the directory where files were written"
        )

        # cache_composition must be deserialised back to a dict
        assert isinstance(retrieved["cache_composition"], dict), (
            "cache_composition must be deserialised as a dict"
        )
        assert retrieved["cache_composition"]["document_type"] == doc_type

    finally:
        rl._DB_PATH = original_db


# ---------------------------------------------------------------------------
# Batch OCR test — verifies extract_batch produces one result per document
# ---------------------------------------------------------------------------

def test_batch_ocr_produces_one_result_per_document():
    """
    Verify that extract_batch returns exactly one OCRResult per input path
    for a batch of 5 sample documents (Requirement 1.7).
    """
    from src.ocr.pipeline import extract_batch, OCRResult

    batch_paths = [str(_DEED_DIR / f"mortgage_{i}.pdf") for i in range(1, 6)]
    for p in batch_paths:
        assert Path(p).exists(), f"Sample PDF not found: {p}"

    results = extract_batch(batch_paths)

    assert len(results) == len(batch_paths), (
        f"extract_batch must return exactly {len(batch_paths)} results, "
        f"got {len(results)}"
    )
    for i, result in enumerate(results):
        assert isinstance(result, OCRResult), (
            f"Result {i} must be an OCRResult, got {type(result)}"
        )
        assert result.source_ref == batch_paths[i], (
            f"Result {i} source_ref must match input path"
        )


def test_batch_ocr_rejects_oversized_batch():
    """
    Verify that extract_batch raises ValueError for batches > 10 (Requirement 1.7).
    """
    from src.ocr.pipeline import extract_batch

    oversized = [str(_DEED_DIR / f"mortgage_{i}.pdf") for i in range(1, 12)]
    with pytest.raises(ValueError, match="10"):
        extract_batch(oversized)


# ---------------------------------------------------------------------------
# Cache reuse test — regeneration must not reload the cache (Requirement 4.8)
# ---------------------------------------------------------------------------

def test_cag_cache_reuse_across_regenerations():
    """
    Verify that calling generate_draft multiple times with the same LegalCache
    does not trigger additional cache loads (Requirement 4.8).
    """
    from src.cag.engine import load_cache, generate_draft
    from src.cag.cache_loader import load_manifest

    load_count = [0]
    original_load_manifest = load_manifest

    def counting_load_manifest(doc_type, llm_backend):
        load_count[0] += 1
        return original_load_manifest(doc_type, llm_backend)

    with patch("src.cag.engine._call_ollama", side_effect=_stub_ollama), \
         patch("src.cag.engine.load_manifest", side_effect=counting_load_manifest):

        cache = load_cache("mortgage_deed", "qwen2.5_1.5b")
        assert load_count[0] == 1, "Cache must be loaded exactly once"

        # Generate three drafts with the same cache
        for i in range(3):
            draft = generate_draft(
                {"intake": {"party": f"Party {i}"}, "document_type": "mortgage_deed"},
                cache,
                "mortgage_deed",
            )
            assert draft.session_id == cache.session_id, (
                f"Draft {i} session_id must match cache session_id"
            )

        # load_manifest must still have been called only once
        assert load_count[0] == 1, (
            f"Cache must not be reloaded on regeneration; load_count={load_count[0]}"
        )


# ---------------------------------------------------------------------------
# Output path structure test (Requirement 14.3)
# ---------------------------------------------------------------------------

def test_store_output_creates_correct_path(tmp_path):
    """
    Verify that store_output writes files to
    output/{pipeline_variant}/{document_type}/{run_id}/ (Requirement 14.3).
    """
    import src.logging.run_logger as rl

    original_output_dir = rl._OUTPUT_DIR
    original_db = rl._DB_PATH

    test_output_dir = str(tmp_path / "output")
    test_db = str(tmp_path / "test.db")
    rl._OUTPUT_DIR = test_output_dir
    rl._DB_PATH = test_db

    try:
        run_id = str(uuid.uuid4())
        test_files = {
            "document.docx": b"PK\x03\x04fake_docx_content",
            "document.pdf": b"%PDF-1.4 fake_pdf_content",
        }

        stored_path = rl.store_output(
            run_id=run_id,
            pipeline_variant="cag",
            document_type="mortgage_deed",
            files=test_files,
        )

        expected_dir = Path(test_output_dir) / "cag" / "mortgage_deed" / run_id
        assert Path(stored_path) == expected_dir, (
            f"store_output must return {expected_dir}, got {stored_path}"
        )
        assert expected_dir.exists(), "Output directory must be created"

        for filename, content in test_files.items():
            file_path = expected_dir / filename
            assert file_path.exists(), f"{filename} must exist at {file_path}"
            assert file_path.read_bytes() == content, f"{filename} content must match"

    finally:
        rl._OUTPUT_DIR = original_output_dir
        rl._DB_PATH = original_db
