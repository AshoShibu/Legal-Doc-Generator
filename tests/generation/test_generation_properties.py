"""
tests/generation/test_generation_properties.py

Property-based tests for the generation pipeline.

Properties tested:
  - Property 9:  Every generated draft contains only cache-grounded citations
  - Property 11: Template slot mapping preserves structural order
  - Property 12: Template versioning prevents overwrite
  - Property 27: Generated document header contains all required metadata
  - Property 28: Citation index is appended to every generated document

**Validates: Requirements 4.3, 4.5, 4.6, 5.2, 5.3, 5.5, 13.1, 13.2, 13.3, 13.5**
"""
from __future__ import annotations

from dataclasses import dataclass, field

from hypothesis import given, settings
from hypothesis import strategies as st

from src.generation.citation_verifier import (
    UNGROUNDED_MARKER,
    verify_citations,
)
from src.generation.document_generator import (
    _build_header,
    _map_llm_to_template,
    generate_document,
)
from src.generation.template_mapper import (
    MarathiSection,
    Template,
    TemplateRegistry,
    TemplateSlot,
)
