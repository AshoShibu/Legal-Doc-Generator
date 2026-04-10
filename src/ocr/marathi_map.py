"""
src/ocr/marathi_map.py — Load bilingual Marathi→English field mapping and normalize labels.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

_DEFAULT_MAP_PATH = Path(__file__).parent.parent.parent / "config" / "marathi_field_map.json"


@lru_cache(maxsize=1)
def load_field_map(map_path: str | None = None) -> dict[str, str]:
    """Load and cache the Marathi→English field mapping table."""
    path = Path(map_path) if map_path else _DEFAULT_MAP_PATH
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def normalize_fields(raw_fields: dict[str, str], map_path: str | None = None) -> dict[str, str]:
    """
    Translate any Marathi field keys to canonical English identifiers.
    Keys not found in the mapping table are passed through unchanged.
    """
    field_map = load_field_map(map_path)
    return {field_map.get(k, k): v for k, v in raw_fields.items()}
