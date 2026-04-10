"""
Property-based tests for the CAG Engine.

Properties tested:
  - Property 7: Legal Cache composition satisfies token budget
  - Property 8: Session log records complete cache composition
  - Property 10: Regeneration does not reload the Legal Cache

Requirements: 3.1, 3.2, 3.3, 3.4, 3.6, 4.8
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

# --------------------------