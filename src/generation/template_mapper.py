"""
src/generation/template_mapper.py — Template Mapper for Maharashtra Legal Document Generation.

Parses .doc/.docx template files, extracts {{SLOT_NAME}} placeholders and structural
sections (recitals, operative clauses, schedules, attestation blocks).

Implements template versioning: never overwrites prior versions.
Preserves Marathi-language sections verbatim (Devanagari Unicode U+0900–U+097F).

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Devanagari Unicode range detection
# ---------------------------------------------------------------------------

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")

# Slot placeholder pattern: {{SLOT_NAME}}
_SLOT_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")

# Default structural schema when no template is found
DEFAULT_SCHEMA_SLOTS = [
    "PARTIES_CLAUSE",
    "RECITALS",
    "OPERATIVE_CLAUSE_1",
    "OPERATIVE_CLAUSE_2",
    "OPERATIVE_CLAUSE_3",
    "SCHEDULE",
    "ATTESTATION",
]

# Filename → document_type mapping
_FILENAME_TYPE_MAP: dict[str, str] = {
    "sale deed": "sale_deed",
    "sale_deed": "sale_deed",
    "mortgage deed": "mortgage_deed",
    "mortgage_deed": "mortgage_deed",
    "power of attorney": "power_of_attorney",
    "power_of_attorney": "power_of_attorney",
    "leave and license": "leave_and_license",
    "leave_and_license": "leave_and_license",
    "leave & license": "leave_and_license",
    "gift deed": "gift_deed",
    "gift_deed": "gift_deed",
    "conveyance deed": "conveyance_deed",
    "conveyance_deed": "conveyance_deed",
    "affidavit": "affidavit",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class MarathiSection:
    """Holds a verbatim Marathi-language text block from a template."""
    text: str
    position: int   # ordinal position in document


@dataclass
class TemplateSlot:
    name: str               # e.g. "PARTIES_CLAUSE", "OPERATIVE_CLAUSE_1"
    position: int           # ordinal position in document
    required: bool
    marathi_equivalent: str | None = None


@dataclass
class Template:
    document_type: str
    version: str            # semantic version, e.g. "1.2.0"
    slots: list[TemplateSlot]
    marathi_sections: list[MarathiSection]  # preserved verbatim


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _infer_document_type(filename: str) -> str:
    """
    Infer document_type from a template filename.
    Returns a snake_case document type string.
    """
    stem = Path(filename).stem.lower().strip()
    # Direct lookup
    if stem in _FILENAME_TYPE_MAP:
        return _FILENAME_TYPE_MAP[stem]
    # Partial match
    for key, doc_type in _FILENAME_TYPE_MAP.items():
        if key in stem:
            return doc_type
    # Fallback: convert filename to snake_case
    return re.sub(r"[^a-z0-9]+", "_", stem).strip("_")


def _is_marathi(text: str) -> bool:
    """Return True if the text contains Devanagari characters."""
    return bool(_DEVANAGARI_RE.search(text))


def _extract_slots_from_paragraphs(paragraphs: list[str]) -> tuple[list[TemplateSlot], list[MarathiSection]]:
    """
    Walk through paragraph texts, extract {{SLOT_NAME}} placeholders and
    Marathi sections.

    Returns (slots, marathi_sections).
    """
    slots: list[TemplateSlot] = []
    marathi_sections: list[MarathiSection] = []
    seen_slots: set[str] = set()
    position = 0

    for para_text in paragraphs:
        if not para_text.strip():
            continue

        # Check for Marathi content
        if _is_marathi(para_text):
            marathi_sections.append(MarathiSection(text=para_text, position=position))
            position += 1
            continue

        # Extract slot placeholders
        for m in _SLOT_RE.finditer(para_text):
            slot_name = m.group(1)
            if slot_name in seen_slots:
                continue
            seen_slots.add(slot_name)

            # Determine if required: slots with "OPERATIVE" or "PARTIES" are required
            required = any(kw in slot_name for kw in ("PARTIES", "OPERATIVE", "RECITAL", "ATTESTATION"))

            slots.append(TemplateSlot(
                name=slot_name,
                position=position,
                required=required,
                marathi_equivalent=None,
            ))
            position += 1

    return slots, marathi_sections


def _parse_docx(path: Path) -> tuple[list[TemplateSlot], list[MarathiSection]]:
    """Parse a .docx file and extract slots and Marathi sections."""
    try:
        from docx import Document
    except ImportError as exc:
        raise ImportError("python-docx is required: pip install python-docx") from exc

    doc = Document(str(path))
    paragraphs: list[str] = []

    # Collect all paragraph texts (including table cells)
    for para in doc.paragraphs:
        paragraphs.append(para.text)

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    paragraphs.append(para.text)

    return _extract_slots_from_paragraphs(paragraphs)


def _parse_doc_fallback(path: Path) -> tuple[list[TemplateSlot], list[MarathiSection]]:
    """
    Fallback parser for .doc files (old binary format).
    Attempts to read as text; returns empty if unreadable.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        paragraphs = text.splitlines()
        return _extract_slots_from_paragraphs(paragraphs)
    except Exception as exc:
        logger.warning("Could not parse .doc file '%s': %s", path, exc)
        return [], []


def _make_default_template(document_type: str, version: str = "1.0.0") -> Template:
    """
    Create a default template using the standard Maharashtra schema:
    Parties → Recitals → Operative Clauses → Schedule → Attestation.
    """
    logger.warning(
        "No template found for document type '%s'. "
        "Using default schema (Parties → Recitals → Operative Clauses → Schedule → Attestation).",
        document_type,
    )
    slots = [
        TemplateSlot(name=name, position=i, required=True)
        for i, name in enumerate(DEFAULT_SCHEMA_SLOTS)
    ]
    return Template(
        document_type=document_type,
        version=version,
        slots=slots,
        marathi_sections=[],
    )


# ---------------------------------------------------------------------------
# TemplateRegistry
# ---------------------------------------------------------------------------


class TemplateRegistry:
    """
    Registry for legal document templates.

    Stores templates keyed by (document_type, version).
    Never overwrites prior versions (Requirement 5.5).
    """

    def __init__(self) -> None:
        # Key: (document_type, version) → Template
        self._store: dict[tuple[str, str], Template] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_templates(self, templates_dir: str) -> dict[tuple[str, str], Template]:
        """
        Load all .doc and .docx template files from *templates_dir*.

        Returns the internal store dict (keyed by (document_type, version)).
        Logs a warning for each file that cannot be parsed.
        """
        dir_path = Path(templates_dir)
        if not dir_path.exists():
            logger.warning(
                "Templates directory '%s' does not exist. No templates loaded.",
                templates_dir,
            )
            return self._store

        loaded = 0
        for path in sorted(dir_path.rglob("*")):
            if path.suffix.lower() not in (".doc", ".docx"):
                continue
            try:
                self._load_single_template(path)
                loaded += 1
            except Exception as exc:
                logger.warning("Failed to load template '%s': %s", path, exc)

        logger.info("Loaded %d template(s) from '%s'.", loaded, templates_dir)
        return self._store

    def get_template(self, document_type: str, version: str | None = None) -> Template:
        """
        Return the template for *document_type*.

        If *version* is None, returns the latest version (highest semver).
        If no template exists for the document type, returns a default template
        and logs a warning.
        """
        matching = [
            (v, t) for (dt, v), t in self._store.items()
            if dt == document_type
        ]

        if not matching:
            return _make_default_template(document_type)

        if version is not None:
            for v, t in matching:
                if v == version:
                    return t
            logger.warning(
                "Version '%s' not found for document type '%s'. "
                "Returning latest version.",
                version, document_type,
            )

        # Return latest version by semver sort
        def _semver_key(v: str) -> tuple[int, ...]:
            try:
                return tuple(int(x) for x in v.split("."))
            except ValueError:
                return (0,)

        matching.sort(key=lambda pair: _semver_key(pair[0]), reverse=True)
        return matching[0][1]

    def register_template(self, template: Template) -> None:
        """
        Add a template to the registry.

        Never overwrites a prior version — if (document_type, version) already
        exists, logs a warning and skips the registration.
        """
        key = (template.document_type, template.version)
        if key in self._store:
            logger.warning(
                "Template ('%s', version '%s') already exists. "
                "Skipping registration to preserve prior version.",
                template.document_type, template.version,
            )
            return
        self._store[key] = template
        logger.debug(
            "Registered template: document_type='%s', version='%s', slots=%d",
            template.document_type, template.version, len(template.slots),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_single_template(self, path: Path) -> None:
        """Parse a single template file and register it."""
        document_type = _infer_document_type(path.name)

        if path.suffix.lower() == ".docx":
            slots, marathi_sections = _parse_docx(path)
        else:
            slots, marathi_sections = _parse_doc_fallback(path)

        # If no slots found, use default schema slots
        if not slots:
            logger.warning(
                "No {{SLOT_NAME}} placeholders found in '%s'. "
                "Using default schema slots.",
                path,
            )
            slots = [
                TemplateSlot(name=name, position=i, required=True)
                for i, name in enumerate(DEFAULT_SCHEMA_SLOTS)
            ]

        # Assign version: use "1.0.0" as default; increment if already registered
        version = self._next_version(document_type)

        template = Template(
            document_type=document_type,
            version=version,
            slots=slots,
            marathi_sections=marathi_sections,
        )
        self.register_template(template)

    def _next_version(self, document_type: str) -> str:
        """
        Determine the next version string for a document type.
        If no version exists, returns "1.0.0".
        Otherwise increments the patch version of the latest.
        """
        existing = [
            v for (dt, v) in self._store
            if dt == document_type
        ]
        if not existing:
            return "1.0.0"

        def _semver_key(v: str) -> tuple[int, ...]:
            try:
                return tuple(int(x) for x in v.split("."))
            except ValueError:
                return (0,)

        latest = max(existing, key=_semver_key)
        parts = latest.split(".")
        try:
            patch = int(parts[-1]) + 1
            return ".".join(parts[:-1] + [str(patch)])
        except (ValueError, IndexError):
            return "1.0.0"
