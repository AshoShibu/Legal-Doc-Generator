"""
src/cag/cache_loader.py — YAML cache manifest loader for the CAG Engine.

Loads cache manifests from config/cache_manifests/, resolves file paths,
computes token counts per entry, and enforces the 90% token budget rule.

Requirements: 3.1, 3.2, 3.3, 3.4
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM context window sizes (in tokens)
# ---------------------------------------------------------------------------

CONTEXT_WINDOWS: dict[str, int] = {
    "llama3_8b": 8192,
    "qwen2_7b": 32768,
    "qwen2.5_7b": 32768,
    "qwen2.5_1.5b": 32768,
    "tinyllama_1.1b": 2048,
    "mixtral_8x7b": 32768,
    "gpt_oss_120b": 128000,
    # Groq cloud — large context windows, all manifest entries fit easily
    "groq_llama3_8b": 128000,
    "groq_llama3_70b": 128000,
    "groq_mixtral": 32768,
}

TOKEN_BUDGET_FRACTION = 0.90  # 90% of context window

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CacheEntry:
    name: str
    path: str                   # resolved absolute path (may not exist yet)
    tokens: int
    priority: str = "normal"    # "normal" | "low"
    content: str = ""           # loaded text content (empty until loaded)
    section_hint: str = ""      # optional: e.g. "122-129" to extract only those sections


@dataclass
class ManifestLoadResult:
    document_type: str
    entries: list[CacheEntry]
    omitted: list[str]          # names of entries dropped due to token budget
    total_tokens: int
    budget_tokens: int          # 90% of context window


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_manifest_dir() -> Path:
    """Return the path to config/cache_manifests/ relative to project root."""
    # Walk up from this file to find the project root (contains config/)
    here = Path(__file__).resolve()
    for parent in [here.parent.parent.parent, here.parent.parent, Path.cwd()]:
        candidate = parent / "config" / "cache_manifests"
        if candidate.exists():
            return candidate
    # Fallback: relative to cwd
    return Path("config") / "cache_manifests"


def _get_corpus_root() -> Path:
    """Return the dataset root path from env or default."""
    return Path(os.environ.get("DATASET_ROOT", "./Maharashtra Legal Document Dataset"))


def _resolve_path(raw_path: str) -> str:
    """
    Resolve a path from the manifest.
    Tries:
      1. Absolute path as-is
      2. Relative to project root (cwd)
      3. Relative to DATASET_ROOT
    Returns the first existing path, or the cwd-relative path as fallback.
    """
    p = Path(raw_path)
    if p.is_absolute() and p.exists():
        return str(p)

    # Relative to cwd
    cwd_path = Path.cwd() / raw_path
    if cwd_path.exists():
        return str(cwd_path)

    # Relative to dataset root
    corpus_path = _get_corpus_root() / raw_path
    if corpus_path.exists():
        return str(corpus_path)

    # Return cwd-relative as fallback (file may not exist yet)
    return str(cwd_path)


def _extract_section_range(content: str, section_hint: str) -> str:
    """
    Extract a range of sections from legal text using a section_hint like "122-129".
    Finds "Section 122" through "Section 129" (inclusive) and returns that slice.
    Falls back to the full content if the range cannot be found.
    """
    try:
        parts = section_hint.strip().split("-")
        start_sec = int(parts[0])
        end_sec = int(parts[1]) if len(parts) > 1 else start_sec
    except (ValueError, IndexError):
        logger.warning("Invalid section_hint '%s' — using full content.", section_hint)
        return content

    # Find start: "Section <start_sec>" or "<start_sec>."
    start_pattern = re.compile(
        rf"(?:^|\n)\s*(?:Section\s+{start_sec}\b|{start_sec}\.\s)",
        re.IGNORECASE,
    )
    # Find end: the line BEFORE "Section <end_sec + 1>" starts
    end_pattern = re.compile(
        rf"(?:^|\n)\s*(?:Section\s+{end_sec + 1}\b|{end_sec + 1}\.\s)",
        re.IGNORECASE,
    )

    start_m = start_pattern.search(content)
    if not start_m:
        logger.warning(
            "section_hint '%s': Section %d not found — using full content.",
            section_hint, start_sec,
        )
        return content

    start_pos = start_m.start()
    end_m = end_pattern.search(content, start_pos)
    end_pos = end_m.start() if end_m else len(content)

    extracted = content[start_pos:end_pos].strip()
    if not extracted:
        return content

    logger.debug(
        "section_hint '%s': extracted %d chars (sections %d–%d).",
        section_hint, len(extracted), start_sec, end_sec,
    )
    return extracted
    """
    Attempt to count tokens from a file.
    Uses a simple character-count approximation (1 token ≈ 4 characters).
    Handles .docx, .pdf, and plain text files.
    Returns None if the file cannot be read.
    """
    content = _load_content(path)
    if not content:
        return None
    return max(1, len(content) // 4)


def _load_content(path: str) -> str:
    """
    Load text content from a file, returning empty string on failure.
    Handles .docx files via python-docx, .pdf via PyMuPDF, plain text otherwise.
    """
    p = Path(path)
    if not p.exists():
        logger.warning("Cache file not found: %s", path)
        return ""

    suffix = p.suffix.lower()

    # --- DOCX ---
    if suffix == ".docx":
        try:
            from docx import Document
            doc = Document(str(p))
            paragraphs = [para.text for para in doc.paragraphs]
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for para in cell.paragraphs:
                            paragraphs.append(para.text)
            return "\n".join(line for line in paragraphs if line.strip())
        except Exception as exc:
            logger.warning("Failed to read DOCX '%s': %s", path, exc)
            return ""

    # --- PDF ---
    if suffix == ".pdf":
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(str(p))
            pages = [page.get_text() for page in doc]
            doc.close()
            return "\n".join(pages).strip()
        except Exception as exc:
            logger.warning("Failed to read PDF '%s': %s", path, exc)
            return ""

    # --- Plain text / other ---
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("Failed to read file '%s': %s", path, exc)
        return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_manifest(document_type: str, llm_backend: str) -> ManifestLoadResult:
    """
    Load and validate a cache manifest for the given document type.

    Steps:
      1. Read YAML from config/cache_manifests/{document_type}.yaml
      2. Resolve file paths for each entry
      3. Use manifest-declared token counts (or compute from file if missing)
      4. Enforce 90% token budget: drop lower-priority entries first, log omissions
      5. Load file content for retained entries

    Args:
        document_type: e.g. "sale_deed", "mortgage_deed"
        llm_backend:   e.g. "llama3_8b", "qwen2_7b", "mixtral_8x7b"

    Returns:
        ManifestLoadResult with retained entries, omitted names, and token totals.

    Raises:
        FileNotFoundError: if the manifest YAML does not exist.
        ValueError: if llm_backend is not recognised.
        ImportError: if PyYAML is not installed.
    """
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("PyYAML is required: pip install pyyaml") from exc

    if llm_backend not in CONTEXT_WINDOWS:
        raise ValueError(
            f"Unknown LLM backend '{llm_backend}'. "
            f"Supported: {list(CONTEXT_WINDOWS.keys())}"
        )

    manifest_dir = _get_manifest_dir()
    manifest_path = manifest_dir / f"{document_type}.yaml"

    if not manifest_path.exists():
        raise FileNotFoundError(
            f"Cache manifest not found: {manifest_path}. "
            f"Create config/cache_manifests/{document_type}.yaml first."
        )

    with open(manifest_path, "r", encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f)

    raw_entries: list[dict] = data.get("priority_order", [])

    # Build CacheEntry list
    entries: list[CacheEntry] = []
    for item in raw_entries:
        name = item.get("name", "unknown")
        raw_path = item.get("path", "")
        resolved = _resolve_path(raw_path)
        priority = item.get("priority", "normal")

        # Token count: use manifest value, fall back to file-based estimate
        declared_tokens = item.get("tokens")
        section_hint = item.get("section_hint", "")

        if declared_tokens is not None:
            tokens = int(declared_tokens)
        else:
            computed = _count_tokens_from_file(resolved)
            tokens = computed if computed is not None else 0
            if tokens == 0:
                logger.warning(
                    "Could not determine token count for '%s' (path: %s). "
                    "Defaulting to 0 — this entry will be included but may "
                    "cause context overflow.",
                    name, resolved,
                )

        entries.append(CacheEntry(
            name=name,
            path=resolved,
            tokens=tokens,
            priority=priority,
            section_hint=section_hint,
        ))

    # Enforce 90% token budget
    context_window = CONTEXT_WINDOWS[llm_backend]
    budget = int(context_window * TOKEN_BUDGET_FRACTION)

    retained, omitted = _enforce_budget(entries, budget)

    if omitted:
        logger.warning(
            "Token budget exceeded for '%s' with backend '%s' "
            "(budget: %d tokens). Omitted documents: %s",
            document_type, llm_backend, budget,
            ", ".join(omitted),
        )

    # Load content for retained entries; apply section_hint if specified
    for entry in retained:
        raw = _load_content(entry.path)
        if entry.section_hint and raw:
            entry.content = _extract_section_range(raw, entry.section_hint)
        else:
            entry.content = raw

    total_tokens = sum(e.tokens for e in retained)

    return ManifestLoadResult(
        document_type=document_type,
        entries=retained,
        omitted=omitted,
        total_tokens=total_tokens,
        budget_tokens=budget,
    )


def _enforce_budget(
    entries: list[CacheEntry],
    budget: int,
) -> tuple[list[CacheEntry], list[str]]:
    """
    Enforce the token budget by dropping lower-priority entries first.

    Strategy:
      - Separate entries into normal-priority and low-priority groups.
      - Accumulate normal-priority entries first (in order).
      - Then add low-priority entries until budget is exhausted.
      - Any entry that would exceed the budget is omitted.

    Returns:
        (retained_entries, omitted_names)
    """
    normal = [e for e in entries if e.priority != "low"]
    low = [e for e in entries if e.priority == "low"]

    retained: list[CacheEntry] = []
    omitted: list[str] = []
    running_total = 0

    for entry in normal:
        if running_total + entry.tokens <= budget:
            retained.append(entry)
            running_total += entry.tokens
        else:
            omitted.append(entry.name)
            logger.warning(
                "Omitting normal-priority entry '%s' (%d tokens) — "
                "would exceed budget of %d tokens (current: %d).",
                entry.name, entry.tokens, budget, running_total,
            )

    for entry in low:
        if running_total + entry.tokens <= budget:
            retained.append(entry)
            running_total += entry.tokens
        else:
            omitted.append(entry.name)
            logger.warning(
                "Omitting low-priority entry '%s' (%d tokens) — "
                "would exceed budget of %d tokens (current: %d).",
                entry.name, entry.tokens, budget, running_total,
            )

    return retained, omitted
