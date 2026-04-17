from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import tarfile
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from src.cag.engine import BACKEND_MODEL_TAGS, generate_draft, load_cache
from src.generation.document_generator import generate_document
from src.generation.exporter import export_document
from src.generation.template_mapper import TemplateRegistry
from src.intake.fact_pattern_builder import build as build_fact_pattern
from src.logging.run_logger import log_run
from src.ocr.pipeline import extract as extract_ocr
from src.pii.redactor import redact

logger = logging.getLogger(__name__)

SCHEMAS_DIR = ROOT / "config" / "intake_schemas"
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "./output"))
FRONTEND_BUILD_DIR = ROOT / "frontend" / "build"
DEFAULT_BACKEND = os.environ.get("LLM_DEFAULT_BACKEND", "groq_llama3_8b")
DATASET_ROOT = Path(os.environ.get("DATASET_ROOT", "./Maharashtra Legal Document Dataset"))
DATASET_BOOTSTRAP_URL = os.environ.get("DATASET_BOOTSTRAP_URL", "").strip()
DATASET_BOOTSTRAP_ARCHIVE = os.environ.get("DATASET_BOOTSTRAP_ARCHIVE", "").strip()

app = FastAPI(
    title="Maharashtra Legal Document API",
    description="Backend API for legal document generation (React-friendly).",
    version="1.0.0",
)

_template_registry = TemplateRegistry()
templates_dir = os.environ.get("TEMPLATES_DIR")
if templates_dir:
    _template_registry.load_templates(templates_dir)


def _load_schema(doc_type: str) -> dict[str, Any]:
    schema_path = SCHEMAS_DIR / f"{doc_type}.json"
    if not schema_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Intake schema not found for document_type '{doc_type}'.",
        )
    try:
        return json.loads(schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Invalid schema JSON for '{doc_type}': {exc}",
        ) from exc


def _supported_document_types() -> list[str]:
    if not SCHEMAS_DIR.exists():
        return []
    return sorted(p.stem for p in SCHEMAS_DIR.glob("*.json"))


def _is_condition_met(field: dict[str, Any], answers: dict[str, Any]) -> bool:
    cond = field.get("conditional_on")
    if not cond:
        return True
    ref_field = cond.get("field")
    ref_value = cond.get("value")
    return answers.get(ref_field) == ref_value


def _missing_required_fields(schema: dict[str, Any], answers: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for section in schema.get("sections", []):
        for field in section.get("fields", []):
            if not field.get("required"):
                continue
            if not _is_condition_met(field, answers):
                continue

            fid = field.get("id")
            value = answers.get(fid)
            if value in (None, "", []):
                missing.append(fid)
    return missing


def _hash_text(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _citation_to_dict(citation: Any) -> dict[str, Any]:
    return {
        "act_name": getattr(citation, "act_name", ""),
        "section": getattr(citation, "section", ""),
        "year": getattr(citation, "year", ""),
        "court_or_legislature": getattr(citation, "court_or_legislature", ""),
        "judgment_year": getattr(citation, "judgment_year", ""),
        "clause_numbers": getattr(citation, "clause_numbers", []),
        "source_document": getattr(citation, "source_document", ""),
    }

def _pipeline_slug(pipeline_variant: str) -> str:
    return pipeline_variant.lower().replace(" ", "_")


cors_origins_env = os.environ.get("CORS_ALLOW_ORIGINS", "*").strip()
cors_origins = ["*"] if cors_origins_env == "*" else [x.strip() for x in cors_origins_env.split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=(cors_origins != ["*"]),
    allow_methods=["*"],
    allow_headers=["*"],
)

if (FRONTEND_BUILD_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_BUILD_DIR / "static"), name="frontend-static")


def _dataset_is_ready() -> bool:
    if not DATASET_ROOT.exists():
        return False
    if DATASET_ROOT.is_file():
        return False
    return any(DATASET_ROOT.iterdir())


def _extract_google_drive_file_id(url: str) -> str | None:
    parsed = urlparse(url)
    if "drive.google.com" not in parsed.netloc:
        return None

    query_id = parse_qs(parsed.query).get("id")
    if query_id:
        return query_id[0]

    parts = [part for part in parsed.path.split("/") if part]
    if "file" in parts and "d" in parts:
        try:
            return parts[parts.index("d") + 1]
        except IndexError:
            return None

    return None


def _download_google_drive_archive(url: str, destination: Path) -> None:
    file_id = _extract_google_drive_file_id(url)
    session = requests.Session()

    if file_id:
        base_url = "https://drive.google.com/uc?export=download"
        response = session.get(base_url, params={"id": file_id}, stream=True, timeout=60)
        response.raise_for_status()

        confirm_token = None
        for key, value in response.cookies.items():
            if key.startswith("download_warning"):
                confirm_token = value
                break

        content_type = response.headers.get("Content-Type", "")
        if confirm_token or "text/html" in content_type:
            response.close()
            response = session.get(
                base_url,
                params={"id": file_id, "confirm": confirm_token or "t"},
                stream=True,
                timeout=60,
            )
            response.raise_for_status()
    else:
        response = session.get(url, stream=True, timeout=60)
        response.raise_for_status()

    with destination.open("wb") as fh:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                fh.write(chunk)


def _extract_archive(archive_path: Path, destination_dir: Path) -> None:
    suffixes = archive_path.suffixes
    if archive_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(destination_dir)
        return

    if suffixes[-2:] == [".tar", ".gz"] or suffixes[-2:] == [".tar", ".bz2"] or suffixes[-2:] == [".tar", ".xz"] or archive_path.suffix.lower() == ".tgz":
        with tarfile.open(archive_path, "r:*") as tf:
            tf.extractall(destination_dir)
        return

    raise RuntimeError(f"Unsupported dataset archive format: {archive_path.name}")


def _ensure_dataset_available() -> None:
    if _dataset_is_ready():
        return

    if not DATASET_BOOTSTRAP_URL:
        logger.warning("DATASET_ROOT is empty and DATASET_BOOTSTRAP_URL is not configured.")
        return

    DATASET_ROOT.mkdir(parents=True, exist_ok=True)
    archive_name = DATASET_BOOTSTRAP_ARCHIVE or Path(urlparse(DATASET_BOOTSTRAP_URL).path).name or "dataset.zip"
    archive_path = DATASET_ROOT.parent / archive_name
    temp_extract_dir = DATASET_ROOT.parent / f"{DATASET_ROOT.name}_extracting"

    if temp_extract_dir.exists():
        shutil.rmtree(temp_extract_dir)
    temp_extract_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Downloading dataset archive from bootstrap URL...")
    _download_google_drive_archive(DATASET_BOOTSTRAP_URL, archive_path)

    logger.info("Extracting dataset archive to %s", temp_extract_dir)
    _extract_archive(archive_path, temp_extract_dir)

    extracted_items = [item for item in temp_extract_dir.iterdir()]
    replacement_source = temp_extract_dir
    if len(extracted_items) == 1 and extracted_items[0].is_dir():
        replacement_source = extracted_items[0]

    if DATASET_ROOT.exists():
        shutil.rmtree(DATASET_ROOT, ignore_errors=True)
    shutil.move(str(replacement_source), str(DATASET_ROOT))
    shutil.rmtree(temp_extract_dir, ignore_errors=True)
    archive_path.unlink(missing_ok=True)
    logger.info("Dataset bootstrap complete at %s", DATASET_ROOT)


@app.on_event("startup")
def bootstrap_runtime_dependencies() -> None:
    _ensure_dataset_available()


class GenerateRequest(BaseModel):
    document_type: str = Field(..., description="e.g. sale_deed")
    user_data: dict[str, Any]
    backend: str = DEFAULT_BACKEND
    pipeline_variant: str = "CAG"
    export_files: bool = True


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/document-types")
def document_types() -> dict[str, Any]:
    return {"document_types": _supported_document_types()}


@app.get("/backends")
def backends() -> dict[str, Any]:
    return {"backends": sorted(BACKEND_MODEL_TAGS.keys())}


@app.get("/intake-schema/{document_type}")
def intake_schema(document_type: str) -> dict[str, Any]:
    return _load_schema(document_type)


@app.post("/extract")
async def extract_upload(
    document_type: str = Form(...),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    schema = _load_schema(document_type)

    suffix = Path(file.filename or "upload.bin").suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = tmp.name
        tmp.write(await file.read())

    try:
        ocr_result = extract_ocr(tmp_path)
        raw_text = json.dumps(ocr_result.fields, ensure_ascii=False)
        redaction_result = redact(raw_text, ocr_result.fields)

        return {
            "status": "success",
            "document_type": document_type,
            "intake_schema": schema,
            "ocr_fields": ocr_result.fields,
            "ocr_confidence": ocr_result.confidence,
            "ocr_warnings": ocr_result.warnings,
            "ocr_errors": ocr_result.errors,
            "redacted_text": redaction_result.redacted_text,
            "audit_log": [
                {
                    "entity_type": e.entity_type,
                    "placeholder": e.placeholder,
                    "char_offset": e.char_offset,
                    "confidence": e.confidence,
                    "flagged_for_review": e.flagged_for_review,
                }
                for e in redaction_result.audit_log
            ],
        }
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

@app.get("/download/{pipeline_slug}/{document_type}/{run_id}/{file_kind}")
def download_generated_file(
    pipeline_slug: str,
    document_type: str,
    run_id: str,
    file_kind: str,
):
    kind = file_kind.lower().strip()
    if kind not in {"docx", "pdf"}:
        raise HTTPException(status_code=400, detail="file_kind must be 'docx' or 'pdf'.")

    file_path = OUTPUT_DIR / pipeline_slug / document_type / run_id / f"{run_id}.{kind}"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Requested file not found: {file_path}")

    media_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if kind == "docx"
        else "application/pdf"
    )
    return FileResponse(path=file_path, media_type=media_type, filename=file_path.name)


@app.post("/generate")
def generate(req: GenerateRequest) -> dict[str, Any]:
    if req.document_type not in _supported_document_types():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported document_type '{req.document_type}'. "
                f"Supported: {_supported_document_types()}"
            ),
        )
    if req.backend not in BACKEND_MODEL_TAGS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported backend '{req.backend}'. "
                f"Supported: {sorted(BACKEND_MODEL_TAGS.keys())}"
            ),
        )

    schema = _load_schema(req.document_type)
    missing_fields = _missing_required_fields(schema, req.user_data)
    if missing_fields:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Missing required intake fields.",
                "missing_fields": missing_fields,
            },
        )

    run_id = str(uuid.uuid4())
    pipeline_slug = _pipeline_slug(req.pipeline_variant)

    try:
        fact_pattern = build_fact_pattern(
            schema=schema,
            ocr_result=None,
            intake_answers=req.user_data,
            redaction_result=None,
        )

        cache = load_cache(req.document_type, req.backend)

        template = _template_registry.get_template(req.document_type)
        slot_names = [slot.name for slot in sorted(template.slots, key=lambda s: s.position)]

        draft = generate_draft(
            fact_pattern=fact_pattern,
            cache=cache,
            doc_type=req.document_type,
            template_slots=slot_names,
        )

        generated = generate_document(
            llm_output=draft.content,
            template=template,
            cache_or_chunks=cache,
            doc_type=req.document_type,
            pipeline_variant=req.pipeline_variant,
            llm_model=req.backend,
            run_id=run_id,
            cache_version=f"session:{cache.session_id[:8]}",
        )

        export_result = None
        output_path = ""
        if req.export_files:
            persistent_dir = OUTPUT_DIR / pipeline_slug / req.document_type / run_id
            persistent_dir.mkdir(parents=True, exist_ok=True)
            export_result = export_document(generated, str(persistent_dir), run_id)
            output_path = str(persistent_dir)

        input_payload = json.dumps(req.user_data, sort_keys=True, ensure_ascii=False, default=str)
        cache_composition = [{"name": d.name, "tokens": d.tokens} for d in cache.documents]
        log_run(
            pipeline_variant=pipeline_slug,
            llm_model=req.backend,
            input_hash=_hash_text(input_payload),
            cache_composition=cache_composition,
            output_hash=_hash_text(generated.content),
            output_path=output_path,
            status="success",
        )

        return {
            "status": "success",
            "run_id": run_id,
            "document_type": req.document_type,
            "pipeline_variant": req.pipeline_variant,
            "backend": req.backend,
            "output": generated.content,
            "header": generated.header,
            "body": generated.body,
            "citation_index": generated.citation_index,
            "citations": [_citation_to_dict(c) for c in generated.citations],
            "ungrounded_clauses": generated.ungrounded_clauses,
            "high_hallucination_risk": generated.high_hallucination_risk,
            "unfilled_slots": generated.unfilled_slots,
            "fact_pattern": fact_pattern,
            "cache_session_id": cache.session_id,
            "export": (
                {
                    "docx_path": export_result.docx_path if export_result else None,
                    "pdf_path": export_result.pdf_path if export_result else None,
                    "docx_url": (
                        f"/download/{pipeline_slug}/{req.document_type}/{run_id}/docx"
                        if export_result and export_result.docx_path
                        else None
                    ),
                    "pdf_url": (
                        f"/download/{pipeline_slug}/{req.document_type}/{run_id}/pdf"
                        if export_result and export_result.pdf_path
                        else None
                    ),
                    "errors": export_result.errors if export_result else [],
                }
            ),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/")
def serve_frontend_index():
    index_path = FRONTEND_BUILD_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="React frontend build not found.")
    return FileResponse(index_path)


@app.get("/{full_path:path}")
def serve_frontend_routes(full_path: str):
    if not full_path or full_path.startswith(("api/", "docs", "redoc", "openapi.json")):
        raise HTTPException(status_code=404, detail="Not found.")

    asset_path = FRONTEND_BUILD_DIR / full_path
    if asset_path.exists() and asset_path.is_file():
        return FileResponse(asset_path)

    index_path = FRONTEND_BUILD_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)

    raise HTTPException(status_code=404, detail="React frontend build not found.")
