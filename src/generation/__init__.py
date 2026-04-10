# Document Generation package

from src.generation.template_mapper import (
    Template,
    TemplateSlot,
    MarathiSection,
    TemplateRegistry,
)
from src.generation.citation_verifier import (
    Citation,
    CitationVerificationResult,
    verify_citations,
)
from src.generation.document_generator import (
    GeneratedDocument,
    generate_document,
)

__all__ = [
    "Template",
    "TemplateSlot",
    "MarathiSection",
    "TemplateRegistry",
    "Citation",
    "CitationVerificationResult",
    "verify_citations",
    "GeneratedDocument",
    "generate_document",
]
