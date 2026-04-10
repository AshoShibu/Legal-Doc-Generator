"""
src/ocr/pipeline.py — OCR Pipeline for Maharashtra legal land records.

Extraction strategy:
  - Text-layer PDFs  → PyMuPDF (fast, lossless)
  - Scanned images / image-only PDFs → DeepSeek OCR via Ollama vision API
  - Fallback for low-quality scans   → Tesseract (optional, graceful degradation)

Mandatory fields: Survey_Number, owner_name, area
"""
from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class OCRResult:
    fields: dict[str, str]          # extracted key→value pairs
    confidence: dict[str, float]    # per-field confidence 0.0–1.0
    source_ref: str                 # original file path
    warnings: list[str] = field(default_factory=list)   # e.g. ["low_dpi: area"]
    errors: list[str] = field(default_factory=list)     # missing mandatory fields


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MANDATORY_FIELDS = ("Survey_Number", "owner_name", "area")
MIN_DPI = 150
MAX_BATCH_SIZE = 10

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_image_file(path: Path) -> bool:
    return path.suffix.lower() in {".jpg", ".jpeg", ".png", ".tiff", ".tif"}


def _extract_text_layer_pdf(path: Path) -> tuple[str, bool]:
    """
    Use PyMuPDF to extract text from a PDF that has a text layer.
    Returns (text, has_text_layer).
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise ImportError("PyMuPDF (fitz) is required: pip install pymupdf") from exc

    doc = fitz.open(str(path))
    pages_text: list[str] = []
    for page in doc:
        pages_text.append(page.get_text())
    doc.close()
    combined = "\n".join(pages_text).strip()
    return combined, bool(combined)


def _get_image_dpi(path: Path) -> float | None:
    """Return the DPI of an image file, or None if it cannot be determined."""
    try:
        from PIL import Image
        with Image.open(str(path)) as img:
            dpi_info = img.info.get("dpi")
            if dpi_info:
                return float(min(dpi_info))
    except Exception:
        pass
    return None


def _get_pdf_image_dpi(path: Path) -> float | None:
    """Return the minimum DPI across all images embedded in a PDF page."""
    try:
        import fitz
        doc = fitz.open(str(path))
        dpis: list[float] = []
        for page in doc:
            for img in page.get_images(full=True):
                xref = img[0]
                base_image = doc.extract_image(xref)
                w = base_image.get("width", 0)
                h = base_image.get("height", 0)
                # fitz doesn't expose DPI directly; estimate from page size vs image size
                rect = page.rect
                if rect.width > 0 and w > 0:
                    dpi_x = w / (rect.width / 72)
                    dpis.append(dpi_x)
        doc.close()
        return min(dpis) if dpis else None
    except Exception:
        return None


def _extract_via_deepseek_ocr(path: Path) -> tuple[str, dict[str, float]]:
    """
    Send image/scanned PDF to DeepSeek OCR via Ollama vision API.
    Returns (extracted_text, confidence_map).
    Falls back to empty string on connection failure so the pipeline
    can still return a structured error rather than crashing.
    """
    import base64
    import json as _json

    try:
        import requests
    except ImportError as exc:
        raise ImportError("requests is required: pip install requests") from exc

    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    model = os.environ.get("DEEPSEEK_OCR_MODEL", "deepseek-r1:7b")

    # Read file as base64
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    payload = {
        "model": model,
        "prompt": (
            "You are an OCR engine for Maharashtra land records. "
            "Extract all key-value pairs from this document image. "
            "Return ONLY a JSON object mapping field names to their values. "
            "Use English field names where possible. "
            "Include confidence scores as a separate 'confidence' key mapping field→float (0.0–1.0)."
        ),
        "images": [b64],
        "stream": False,
    }

    try:
        resp = requests.post(f"{ollama_url}/api/generate", json=payload, timeout=60)
        resp.raise_for_status()
        raw = resp.json().get("response", "")
        # Try to parse JSON from the response
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if json_match:
            parsed = _json.loads(json_match.group())
            confidence = parsed.pop("confidence", {})
            return _json.dumps(parsed), confidence
        return raw, {}
    except Exception:
        return "", {}


def _parse_key_value_text(text: str) -> dict[str, str]:
    """
    Heuristic parser: extract key: value pairs from plain text.
    Handles common formats like "Survey Number: 123/4A" or "Survey Number - 123/4A".
    """
    fields: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        # Match "Key: Value" or "Key - Value" or "Key = Value"
        m = re.match(r"^([^:\-=]+?)[\s]*[:\-=][\s]*(.+)$", line)
        if m:
            key = m.group(1).strip()
            val = m.group(2).strip()
            if key and val:
                fields[key] = val
    return fields


def _validate_mandatory_fields(fields: dict[str, str]) -> list[str]:
    """Return a list of error strings for any missing mandatory fields."""
    errors: list[str] = []
    for mf in MANDATORY_FIELDS:
        if not fields.get(mf):
            errors.append(
                f"Mandatory field '{mf}' is missing or empty. "
                f"Please verify the source document or enter the value manually."
            )
    return errors


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract(file_path: str) -> OCRResult:
    """
    Extract structured fields from a land record PDF or image.

    Strategy:
      1. If the file is a PDF with a text layer → PyMuPDF
      2. If the file is an image or a scanned PDF → DeepSeek OCR via Ollama
      3. Normalize Marathi field labels → canonical English identifiers
      4. Validate mandatory fields; populate errors if missing
      5. Detect low DPI; populate warnings if below MIN_DPI
    """
    from src.ocr.marathi_map import normalize_fields

    path = Path(file_path)
    if not path.exists():
        return OCRResult(
            fields={},
            confidence={},
            source_ref=file_path,
            errors=[f"File not found: {file_path}"],
        )

    raw_fields: dict[str, str] = {}
    confidence: dict[str, float] = {}
    warnings: list[str] = []
    is_scanned = False

    # ---- Step 1: Extract text ----
    if path.suffix.lower() == ".pdf":
        text, has_text_layer = _extract_text_layer_pdf(path)
        if has_text_layer:
            raw_fields = _parse_key_value_text(text)
            # Default confidence for text-layer extraction
            confidence = {k: 0.95 for k in raw_fields}
        else:
            is_scanned = True
    else:
        is_scanned = True

    if is_scanned:
        ocr_text, ocr_confidence = _extract_via_deepseek_ocr(path)
        if ocr_text:
            try:
                import json as _json
                parsed = _json.loads(ocr_text)
                raw_fields = {str(k): str(v) for k, v in parsed.items()}
            except Exception:
                raw_fields = _parse_key_value_text(ocr_text)
        confidence = {k: float(ocr_confidence.get(k, 0.80)) for k in raw_fields}

    # ---- Step 2: Normalize Marathi labels ----
    normalized_fields = normalize_fields(raw_fields)
    normalized_confidence = {
        normalize_fields({k: ""})[k] if k in normalize_fields({k: ""}) else k: v
        for k, v in confidence.items()
    }
    # Simpler re-key using the same map
    from src.ocr.marathi_map import load_field_map
    field_map = load_field_map()
    normalized_confidence = {field_map.get(k, k): v for k, v in confidence.items()}

    # ---- Step 3: DPI check ----
    low_dpi_fields: list[str] = []
    dpi: float | None = None
    if is_scanned or _is_image_file(path):
        if _is_image_file(path):
            dpi = _get_image_dpi(path)
        else:
            dpi = _get_pdf_image_dpi(path)

        if dpi is not None and dpi < MIN_DPI:
            # Flag all fields that were extracted from the low-DPI source
            low_dpi_fields = list(normalized_fields.keys())
            if low_dpi_fields:
                warnings.append(f"low_dpi: {', '.join(low_dpi_fields)}")
            else:
                warnings.append(f"low_dpi: all fields (DPI={dpi:.0f}, minimum={MIN_DPI})")

    # ---- Step 4: Mandatory field validation ----
    errors = _validate_mandatory_fields(normalized_fields)

    return OCRResult(
        fields=normalized_fields,
        confidence=normalized_confidence,
        source_ref=file_path,
        warnings=warnings,
        errors=errors,
    )


def extract_batch(file_paths: list[str]) -> list[OCRResult]:
    """
    Extract structured fields from a batch of up to MAX_BATCH_SIZE documents.

    Raises:
        ValueError: if more than MAX_BATCH_SIZE paths are provided.
    """
    if len(file_paths) > MAX_BATCH_SIZE:
        raise ValueError(
            f"Batch size {len(file_paths)} exceeds the maximum of {MAX_BATCH_SIZE} documents. "
            f"Please split your upload into smaller batches."
        )
    return [extract(fp) for fp in file_paths]
