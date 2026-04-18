"""
src/generation/exporter.py — DOCX and PDF export for Maharashtra Legal Document Generation.

Exports a GeneratedDocument to:
  - DOCX: python-docx with citation hyperlinks as Word bookmarks
  - PDF:  reportlab with clickable citation anchors

Both formats include the document header and citation index.

Requirements: 4.7, 10.4
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from src.generation.citation_verifier import Citation
from src.generation.document_generator import GeneratedDocument

logger = logging.getLogger(__name__)

# Inline citation marker pattern: [1], [2], etc. (1–3 digits only, not years like [1882])
_CITATION_MARKER_RE = re.compile(r"\[(\d{1,3})\]")

# Devanagari Unicode range — used to detect Marathi text for font selection
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


@dataclass
class ExportResult:
    docx_path: str | None
    pdf_path: str | None
    errors: list[str]


# ---------------------------------------------------------------------------
# DOCX export
# ---------------------------------------------------------------------------


def export_docx(document: GeneratedDocument, output_path: str) -> str:
    """
    Export *document* to a .docx file at *output_path*.

    Citation markers [N] in the body are converted to Word bookmarks
    and hyperlinks pointing to the citation index entries.

    Args:
        document:    The assembled GeneratedDocument.
        output_path: Destination file path (must end in .docx).

    Returns:
        The resolved output path string.
    """
    try:
        from docx import Document
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
        from docx.shared import Pt, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
    except ImportError as exc:
        raise ImportError("python-docx is required: pip install python-docx") from exc

    doc = Document()

    # ---- Header section ----
    _add_header_paragraphs(doc, document.header)

    # ---- Body ----
    _add_body_with_bookmarks(doc, document.body, document.citations)

    # Phase 1.5: Apply party name underlining (gated by feature flag)
    from config.settings import settings
    if settings.enable_underline_formatting:
        try:
            from src.generation.formatting_engine import get_underline_ranges, apply_underline_docx
            doc_text = document.body
            ranges = document.underline_ranges or get_underline_ranges(doc_text, document.doc_type, parties_only=True)
            if ranges:
                apply_underline_docx(doc, ranges)
        except Exception as exc:
            logger.warning("DOCX underlining failed, exporting without underlining: %s", exc)

    # ---- Citation index ----
    _add_citation_index_text(doc, document.citation_index, document.citations)

    # Ensure output directory exists
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    doc.save(str(out))
    logger.info("DOCX exported to '%s'.", out)
    return str(out)


def _add_header_paragraphs(doc, header_text: str) -> None:
    """Add the document header as styled paragraphs."""
    from docx.shared import Pt, RGBColor

    for line in header_text.splitlines():
        p = doc.add_paragraph()
        run = p.add_run(line)
        run.font.size = Pt(9)
        # Highlight disclaimer line
        if "DISCLAIMER" in line or "WARNING" in line:
            run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)
            run.bold = True
        else:
            run.font.color.rgb = RGBColor(0x44, 0x44, 0x44)

    # Separator
    doc.add_paragraph("-" * 80)


def _add_body_with_bookmarks(doc, body_text: str, citations: list[Citation]) -> None:
    """
    Add body paragraphs. Citation markers [N] are replaced with
    hyperlink-style runs that reference bookmark targets in the citation index.
    """
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    from docx.shared import Pt, RGBColor

    for para_text in body_text.split("\n"):
        if not para_text.strip():
            doc.add_paragraph()
            continue

        p = doc.add_paragraph()
        _add_runs_with_citation_links(p, _clean_for_docx(para_text), citations)


def _add_runs_with_citation_links(paragraph, text: str, citations: list[Citation]) -> None:
    """
    Split *text* on citation markers [N] and add runs.
    Citation markers become hyperlink-style runs with a bookmark reference.
    """
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    from docx.shared import RGBColor

    last_end = 0
    for m in _CITATION_MARKER_RE.finditer(text):
        # Plain text before the marker
        if m.start() > last_end:
            paragraph.add_run(text[last_end:m.start()])

        marker_num = int(m.group(1))
        run = paragraph.add_run(m.group(0))
        run.font.color.rgb = RGBColor(0x00, 0x00, 0xCC)
        run.bold = True

        # Add a bookmark reference (hyperlink to citation index bookmark)
        _add_bookmark_ref(paragraph, run, f"citation_{marker_num}")

        last_end = m.end()

    # Remaining text
    if last_end < len(text):
        paragraph.add_run(text[last_end:])


def _add_bookmark_ref(paragraph, run, bookmark_name: str) -> None:
    """
    Attach a Word internal hyperlink (bookmark reference) to *run*.
    This creates a clickable link within the document pointing to the
    bookmark in the citation index.
    """
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    # Build <w:hyperlink w:anchor="bookmark_name"> wrapping the run
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("w:anchor"), bookmark_name)

    # Move the run's XML element inside the hyperlink
    run_elem = run._r
    run_elem.getparent().remove(run_elem)
    hyperlink.append(run_elem)
    paragraph._p.append(hyperlink)


def _add_citation_index_text(doc, citation_index_text: str, citations: list[Citation]) -> None:
    """
    Append the final citation index exactly as generated so export output
    matches the preview. For grounded citations, preserve bookmark targets
    so inline [N] links can still jump to the matching entry.
    """
    from docx.shared import Pt

    doc.add_paragraph("-" * 80)

    normalized = citation_index_text.strip("\n")
    if not normalized:
        doc.add_paragraph("(No citations found in this document.)")
        return

    bookmark_numbers = {str(i) for i in range(1, len(citations) + 1)}

    for raw_line in normalized.splitlines():
        p = doc.add_paragraph()
        line = _clean_for_docx(raw_line)
        marker_match = _CITATION_MARKER_RE.match(line.strip())
        if marker_match and marker_match.group(1) in bookmark_numbers:
            _insert_bookmark(p, f"citation_{marker_match.group(1)}")

        run = p.add_run(line)
        run.font.size = Pt(11 if "CITATION INDEX" in line else 9)
        if "CITATION INDEX" in line:
            run.bold = True


def _add_citation_index(doc, citations: list[Citation]) -> None:
    """
    Append the citation index section with Word bookmarks on each entry.
    Each entry gets a bookmark named `citation_N` so hyperlinks resolve.
    """
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    from docx.shared import Pt, RGBColor

    doc.add_paragraph("-" * 80)
    heading = doc.add_paragraph("CITATION INDEX")
    if heading.runs:
        heading.runs[0].bold = True
        heading.runs[0].font.size = Pt(11)

    if not citations:
        doc.add_paragraph("(No citations found in this document.)")
        return

    for i, citation in enumerate(citations, start=1):
        p = doc.add_paragraph()

        # Insert bookmark start/end around the citation number
        _insert_bookmark(p, f"citation_{i}")

        clause_refs = (
            ", ".join(str(c + 1) for c in citation.clause_numbers)
            if citation.clause_numbers else "N/A"
        )
        judgment_year = citation.judgment_year or citation.year
        entry_text = (
            f"[{i}] [{citation.act_name}, {citation.year}] "
            f"Section {citation.section}, "
            f"[{citation.court_or_legislature}], [{judgment_year}]"
            f"  — Cited in clause(s): {clause_refs}"
        )
        run = p.add_run(entry_text)
        run.font.size = Pt(9)


def _insert_bookmark(paragraph, bookmark_name: str) -> None:
    """Insert a Word bookmark at the start of *paragraph*."""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    bm_start = OxmlElement("w:bookmarkStart")
    bm_start.set(qn("w:id"), str(abs(hash(bookmark_name)) % 100000))
    bm_start.set(qn("w:name"), bookmark_name)

    bm_end = OxmlElement("w:bookmarkEnd")
    bm_end.set(qn("w:id"), str(abs(hash(bookmark_name)) % 100000))

    paragraph._p.insert(0, bm_start)
    paragraph._p.append(bm_end)


# ---------------------------------------------------------------------------
# PDF export
# ---------------------------------------------------------------------------


def export_pdf(document: GeneratedDocument, output_path: str) -> str:
    """
    Export *document* to a PDF file at *output_path* using reportlab.

    Citation markers [N] in the body are rendered as clickable internal
    links pointing to the citation index entries.

    Args:
        document:    The assembled GeneratedDocument.
        output_path: Destination file path (must end in .pdf).

    Returns:
        The resolved output path string.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, HRFlowable,
        )
        from reportlab.platypus.flowables import AnchorFlowable
    except ImportError as exc:
        raise ImportError("reportlab is required: pip install reportlab") from exc

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    pdf = SimpleDocTemplate(
        str(out),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    styles = getSampleStyleSheet()
    story = _build_pdf_story(document, styles)

    pdf.build(story)
    logger.info("PDF exported to '%s'.", out)
    return str(out)


def _build_pdf_story(document: GeneratedDocument, styles) -> list:
    """Build the reportlab story (list of Flowables) for the document."""
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer, HRFlowable
    from reportlab.platypus.flowables import AnchorFlowable
    from reportlab.lib.enums import TA_LEFT

    story = []

    # Styles
    header_style = ParagraphStyle(
        "HeaderStyle",
        parent=styles["Normal"],
        fontSize=8,
        textColor=colors.HexColor("#444444"),
        spaceAfter=2,
    )
    disclaimer_style = ParagraphStyle(
        "DisclaimerStyle",
        parent=styles["Normal"],
        fontSize=8,
        textColor=colors.HexColor("#C00000"),
        spaceAfter=2,
    )
    body_style = ParagraphStyle(
        "BodyStyle",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        spaceAfter=4,
    )
    citation_heading_style = ParagraphStyle(
        "CitationHeading",
        parent=styles["Heading2"],
        fontSize=11,
        spaceAfter=6,
    )
    citation_entry_style = ParagraphStyle(
        "CitationEntry",
        parent=styles["Normal"],
        fontSize=8,
        leading=12,
        spaceAfter=3,
    )

    # ---- Header ----
    for line in document.header.splitlines():
        if not line.strip():
            story.append(Spacer(1, 2))
            continue
        style = disclaimer_style if ("DISCLAIMER" in line or "WARNING" in line) else header_style
        story.append(Paragraph(_escape_xml(line), style))

    story.append(HRFlowable(width="100%", thickness=0.5, spaceAfter=6))

    # ---- Body ----
    from config.settings import settings as _settings
    _underline_ranges: list[tuple[int, int]] = []
    if _settings.enable_underline_formatting:
        try:
            from src.generation.formatting_engine import get_underline_ranges, underline_parties_pdf_html
            _underline_ranges = document.underline_ranges or get_underline_ranges(document.body, document.doc_type, parties_only=True)
        except Exception as exc:
            logger.warning("PDF underlining setup failed: %s", exc)

    # In Phase 1.5 mode (citations=[]), [N] markers are plain text — no anchor targets exist.
    # Only linkify when structured citations are present (Phase 1.0 mode).
    _linkify = bool(document.citations)

    _body_cursor = 0
    for para_text in document.body.split("\n"):
        para_len = len(para_text) + 1  # +1 for the \n
        if not para_text.strip():
            story.append(Spacer(1, 4))
            _body_cursor += para_len
            continue

        # Apply underlining BEFORE XML escaping so <u> tags survive into reportlab
        if _underline_ranges:
            try:
                para_start = _body_cursor
                para_end = _body_cursor + len(para_text)
                local_ranges = [
                    (max(0, s - para_start), min(len(para_text), e - para_start))
                    for s, e in _underline_ranges
                    if s < para_end and e > para_start
                ]
                if local_ranges:
                    # Inject <u> tags on raw text, then escape the non-tag portions
                    underlined = underline_parties_pdf_html(para_text, local_ranges)
                    # Split on <u>...</u> to escape only the non-underlined parts
                    _U_SPLIT = re.compile(r"(<u>.*?</u>)", re.DOTALL)
                    parts = _U_SPLIT.split(underlined)
                    para_html = "".join(
                        p if p.startswith("<u>") else _escape_xml(p)
                        for p in parts
                    )
                    para_html = _citation_markers_to_links(para_html) if _linkify else para_html
                else:
                    para_html = _citation_markers_to_links(para_text) if _linkify else _escape_xml(para_text)
            except Exception as exc:
                logger.warning("PDF paragraph underlining failed: %s", exc)
                para_html = _citation_markers_to_links(para_text) if _linkify else _escape_xml(para_text)
        else:
            para_html = _citation_markers_to_links(para_text) if _linkify else _escape_xml(para_text)

        story.append(Paragraph(para_html, body_style))
        _body_cursor += para_len

    story.append(HRFlowable(width="100%", thickness=0.5, spaceBefore=8, spaceAfter=6))

    # ---- Citation index ----
    # Phase 1.5 mode: citation index already embedded in body text — skip anchor section.
    # Phase 1.0 mode: render structured citations with internal anchor links.
    if document.citations and not document.citation_index.strip("\n"):
        story.append(Paragraph("CITATION INDEX", citation_heading_style))
        for i, citation in enumerate(document.citations, start=1):
            story.append(AnchorFlowable(f"citation_{i}"))
            clause_refs = (
                ", ".join(str(c + 1) for c in citation.clause_numbers)
                if citation.clause_numbers else "N/A"
            )
            judgment_year = citation.judgment_year or citation.year
            entry = (
                f"[{i}] [{_escape_xml(citation.act_name)}, {citation.year}] "
                f"Section {_escape_xml(citation.section)}, "
                f"[{_escape_xml(citation.court_or_legislature)}], [{judgment_year}]"
                f" — Cited in clause(s): {clause_refs}"
            )
            story.append(Paragraph(entry, citation_entry_style))

    normalized_index = document.citation_index.strip("\n")
    if normalized_index:
        # Remove any fallback citation section that may have been added above.
        while story and getattr(story[-1], "__class__", None).__name__ == "Paragraph":
            break

    return story


def _citation_markers_to_links(text: str) -> str:
    """
    Replace [N] citation markers in *text* with reportlab internal link tags.
    e.g. [1] → <a href="#citation_1" color="blue">[1]</a>
    """
    def _replace(m: re.Match) -> str:
        n = m.group(1)
        return f'<a href="#citation_{n}" color="blue">[{n}]</a>'

    escaped = _escape_xml(text)
    # Re-apply link replacement on escaped text (markers survive escaping)
    return _CITATION_MARKER_RE.sub(
        lambda m: f'<a href="#citation_{m.group(1)}" color="blue">[{m.group(1)}]</a>',
        escaped,
    )


_MD_BOLD_STRIP_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_MD_ITALIC_STRIP_RE = re.compile(r"\*(.+?)\*", re.DOTALL)


def _clean_for_docx(text: str) -> str:
    """Strip markdown bold/italic and replace rupee symbol for DOCX plain-text runs."""
    text = _MD_BOLD_STRIP_RE.sub(r"\1", text)
    text = _MD_ITALIC_STRIP_RE.sub(r"\1", text)
    return text.replace("\u20b9", "Rs.")


def _escape_xml(text: str) -> str:
    """Escape XML special characters for reportlab Paragraph content.
    Also strips markdown bold/italic and replaces the rupee symbol."""
    text = _MD_BOLD_STRIP_RE.sub(r"\1", text)
    text = _MD_ITALIC_STRIP_RE.sub(r"\1", text)
    text = text.replace("\u20b9", "Rs.")
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------------------------------------------------------------------------
# Combined export helper
# ---------------------------------------------------------------------------


def export_document(
    document: GeneratedDocument,
    output_dir: str,
    base_filename: str | None = None,
) -> ExportResult:
    """
    Export *document* to both DOCX and PDF in *output_dir*.

    Args:
        document:      The assembled GeneratedDocument.
        output_dir:    Directory to write files into.
        base_filename: Base name without extension (defaults to run_id).

    Returns:
        ExportResult with paths and any errors encountered.
    """
    base = base_filename or document.run_id
    docx_path = str(Path(output_dir) / f"{base}.docx")
    pdf_path = str(Path(output_dir) / f"{base}.pdf")
    errors: list[str] = []

    # DOCX
    try:
        export_docx(document, docx_path)
    except Exception as exc:
        logger.error("DOCX export failed: %s", exc)
        errors.append(f"DOCX export failed: {exc}")
        docx_path = None  # type: ignore[assignment]

    # PDF
    try:
        export_pdf(document, pdf_path)
    except Exception as exc:
        logger.error("PDF export failed: %s", exc)
        errors.append(f"PDF export failed: {exc}")
        pdf_path = None  # type: ignore[assignment]

    return ExportResult(docx_path=docx_path, pdf_path=pdf_path, errors=errors)
