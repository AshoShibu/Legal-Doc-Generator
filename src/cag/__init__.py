"""
Cache-Augmented Generation (CAG) package.

Public API:
  from src.cag.engine import load_cache, generate_draft, LegalCache, DraftResult, Citation
  from src.cag.cache_loader import CacheEntry, ManifestLoadResult, CONTEXT_WINDOWS
"""
from src.cag.cache_loader import CacheEntry, ManifestLoadResult, CONTEXT_WINDOWS
from src.cag.engine import (
    Citation,
    DraftResult,
    LegalCache,
    clear_session,
    generate_draft,
    get_session_cache,
    load_cache,
)

__all__ = [
    "CacheEntry",
    "ManifestLoadResult",
    "CONTEXT_WINDOWS",
    "Citation",
    "DraftResult",
    "LegalCache",
    "load_cache",
    "generate_draft",
    "get_session_cache",
    "clear_session",
]
