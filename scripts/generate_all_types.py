"""
scripts/generate_all_types.py
Run the full CAG pipeline for all 7 document types and write real output files
into output/cag/{doc_type}/{run_id}/ so the directory structure can be verified.

LLM is stubbed — no Ollama server required.
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CORPUS = ROOT / "Maharashtra Legal Document Dataset" / "Mortage and land deed"
OUTPUT_ROOT = ROOT / "output"

TEST_CASES = [
    ("sale_deed",         CORPUS / "sale_deed_001.pdf"),
    ("mortgage_deed",     CORPUS / "mortgage_1.pdf"),
    ("gift_deed",         CORPUS / "mortgage_2.pdf"),
    ("affidavit",         CORPUS / "mortgage_3.pdf"),
    ("conveyance_deed",   CORPUS / "mortgage_4.pdf"),
    ("power_of_attorney", CORPUS / "mortgage_5.pdf"),
    ("leave_and_license", CORPUS / "mortgage_6.pdf"),
]


def _stub_llm(doc_type: str) -> str:
    label = doc_type.replace("_", " ").title()
    return f"""\
{label.upper()}

This {label} is executed between <PETITIONER_1> (Party A) and <RESPONDENT_1> (Party B).

RECITALS
Whereas Party A is the absolute owner of the property described in the Schedule.
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


def run_one(doc_type: str, pdf_path: Path) -> dict:
    file_bytes = pdf_path.read_bytes()
    run_id = str(uuid.uuid4())

    # ---- OCR ----
    from src.ocr.pipeline import extract
    with tempfile.NamedTemporaryFile(suffix=pdf_path.suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    try:
        ocr = extract(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    # ---- PII ----
    from src.pii.redactor import redact
    raw_text = json.dumps(ocr.fields, ensure_ascii=False)
    pii = redact(raw_text, ocr.fields)

    # ---- CAG (stubbed LLM) ----
    from src.cag.engine import load_cache, generate_draft
    with patch("src.cag.engine._call_ollama", return_value=_stub_llm(doc_type)):
        cache = load_cache(doc_type, "mixtral_8x7b")
        draft = generate_draft(pii.redacted_text or raw_text, cache, doc_type)

    # ---- Document Generator ----
    from src.generation.document_generator import generate_document
    generated = generate_document(
        llm_output=draft.content,
        template=None,
        cache_or_chunks=cache,
        doc_type=doc_type,
        pipeline_variant="CAG",
        llm_model="mixtral_8x7b",
        run_id=run_id,
        cache_version=f"session:{cache.session_id[:8]}",
    )

    # ---- Export to real output/ directory ----
    from src.generation.exporter import export_document
    out_dir = OUTPUT_ROOT / "cag" / doc_type / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    exp = export_document(generated, str(out_dir), run_id)

    # ---- Run Logger ----
    import src.logging.run_logger as rl
    cache_comp = [{"name": d.name, "tokens": d.tokens} for d in cache.documents]
    docx_bytes = Path(exp.docx_path).read_bytes() if exp.docx_path and Path(exp.docx_path).exists() else b""
    pdf_bytes  = Path(exp.pdf_path).read_bytes()  if exp.pdf_path  and Path(exp.pdf_path).exists()  else b""
    rl.log_run(
        pipeline_variant="cag",
        llm_model="mixtral_8x7b",
        input_hash=hashlib.sha256(file_bytes).hexdigest(),
        cache_composition=cache_comp,
        output_hash=hashlib.sha256(docx_bytes or pdf_bytes).hexdigest(),
        output_path=str(out_dir),
        status="success",
    )

    docx_size = Path(exp.docx_path).stat().st_size if exp.docx_path and Path(exp.docx_path).exists() else 0
    pdf_size  = Path(exp.pdf_path).stat().st_size  if exp.pdf_path  and Path(exp.pdf_path).exists()  else 0

    return {
        "doc_type":   doc_type,
        "run_id":     run_id,
        "out_dir":    out_dir,
        "docx_path":  exp.docx_path,
        "pdf_path":   exp.pdf_path,
        "docx_size":  docx_size,
        "pdf_size":   pdf_size,
        "cache_docs": len(cache.documents),
        "cache_tok":  cache.total_tokens,
        "citations":  len(generated.citations),
        "ungrounded": len(generated.ungrounded_clauses),
        "errors":     exp.errors,
    }


def main() -> None:
    print("=" * 70)
    print("Maharashtra Legal Document Generation — Full Pipeline Run")
    print(f"Output root: {OUTPUT_ROOT}")
    print("=" * 70)

    results = []
    for doc_type, pdf_path in TEST_CASES:
        print(f"\n[{doc_type}]")
        if not pdf_path.exists():
            print(f"  SKIP — sample PDF not found: {pdf_path}")
            continue
        r = run_one(doc_type, pdf_path)
        results.append(r)
        status = "OK" if r["docx_size"] > 0 and r["pdf_size"] > 0 else "FAIL"
        print(f"  Status   : {status}")
        print(f"  Run ID   : {r['run_id']}")
        print(f"  DOCX     : output/cag/{r['doc_type']}/{r['run_id']}/{r['run_id']}.docx  ({r['docx_size']:,} bytes)")
        print(f"  PDF      : output/cag/{r['doc_type']}/{r['run_id']}/{r['run_id']}.pdf   ({r['pdf_size']:,} bytes)")
        print(f"  Cache    : {r['cache_docs']} docs, {r['cache_tok']:,} tokens")
        print(f"  Citations: {r['citations']} grounded, {r['ungrounded']} ungrounded clauses")
        if r["errors"]:
            print(f"  Warnings : {r['errors']}")

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    all_ok = True
    for r in results:
        ok = r["docx_size"] > 0 and r["pdf_size"] > 0
        all_ok = all_ok and ok
        tag = "PASS" if ok else "FAIL"
        print(f"  [{tag}]  {r['doc_type']:<25s}  DOCX={r['docx_size']:>8,}B  PDF={r['pdf_size']:>7,}B")

    print()
    print("Output directory tree:")
    cag_dir = OUTPUT_ROOT / "cag"
    for doc_dir in sorted(cag_dir.iterdir()):
        if not doc_dir.is_dir():
            continue
        for run_dir in sorted(doc_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            for f in sorted(run_dir.iterdir()):
                rel = f.relative_to(OUTPUT_ROOT)
                print(f"  output/{rel}  ({f.stat().st_size:,} bytes)")

    print()
    print("OVERALL:", "PASS" if all_ok else "FAIL")


if __name__ == "__main__":
    main()
