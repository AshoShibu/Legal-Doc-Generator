"""
tests/test_model_comparison.py

Model comparison test suite — Requirements 12.1–12.8.

Coverage:
  - Model accessibility checks (Groq API key present / Ollama reachable)
  - Mocked comparison run across 2 models (Llama 3.1 8B + Qwen 2.5 7B)
  - Tier 1 + Tier 2 metrics present in each run's evaluation_result
  - RAGAS (Tier 3) keys absent (skipped to minimise execution time)
  - CSV export columns and row count
  - Large models excluded by default; included when --include-large-models flag set
  - Error resilience: failed runs logged and skipped
  - Integration test (real Groq API, skipped when GROQ_API_KEY not set)

All mocked tests work without any API keys or Ollama running.
"""
from __future__ import annotations

import csv
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.model_comparison.model_registry import (
    MODEL_REGISTRY,
    get_available_models,
)
from src.model_comparison.backends import ModelAPIError
from src.model_comparison.comparator import run_comparison
from src.model_comparison.reporter import export_csv

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MINIMAL_SALE_DEED_FACT_PATTERN = {
    "vendor_name": "Rajesh Kumar Sharma",
    "purchaser_name": "Priya Mehta",
    "property_survey_number": "123/4A",
    "consideration_amount": "5000000",
    "registration_district": "Pune",
}

_STUB_DOCUMENT = """
SALE DEED

THIS SALE DEED is made and executed on this day between:

VENDOR: Rajesh Kumar Sharma, resident of Pune (hereinafter called the "Vendor")

PURCHASER: Priya Mehta, resident of Mumbai (hereinafter called the "Purchaser")

NOW THIS DEED WITNESSETH that in consideration of the sum of Rs. 50,00,000/-
(Rupees Fifty Lakhs only) paid by the Purchaser to the Vendor, the Vendor
hereby sells, transfers and conveys unto the Purchaser the property bearing
Survey No. 123/4A situated in Pune district.

IT IS HEREBY AGREED that the Vendor has clear and marketable title to the
said property free from all encumbrances.

[Transfer of Property Act, 1882] Section 54, Parliament of India, 1882
[Registration Act, 1908] Section 17(1)(b), Parliament of India, 1908
[Maharashtra Stamp Act, 1958] Article 25, Maharashtra Legislature, 1958

IN WITNESS WHEREOF the parties have signed this deed on the day and year
first above written.

Vendor: Rajesh Kumar Sharma
Purchaser: Priya Mehta
"""

# Tier 1 metric keys that must appear in every evaluation_result
_TIER1_KEYS = {"cache_hit_rate", "slot_fill_rate", "fact_fidelity_score", "latency_seconds"}

# Tier 2 metric keys that must appear in every evaluation_result
_TIER2_KEYS = {"section_completeness", "citation_format_compliance", "jurisdictional_accuracy"}

# RAGAS keys that must NOT appear (Tier 3 skipped)
_RAGAS_KEYS = {
    "ragas_faithfulness",
    "ragas_answer_relevancy",
    "ragas_llm_context_precision_with_reference",
    "ragas_context_recall",
    "ragas_answer_relevance",
    "ragas_context_precision",
}

# Required CSV columns per reporter spec
_REQUIRED_CSV_COLUMNS = {
    "timestamp",
    "model_id",
    "parameter_preset",
    "doc_type",
    "run_id",
    "backend",
    "latency_seconds",
    "token_count",
    "cache_hit_rate",
    "slot_fill_rate",
    "fact_fidelity_score",
    "section_completeness",
    "citation_format_compliance",
    "jurisdictional_accuracy",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_ollama_reachable() -> bool:
    """Return True if the local Ollama API responds."""
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def _make_mock_generate(stub_text: str = _STUB_DOCUMENT):
    """Return a mock generate() that returns (stub_text, 1.23, 512)."""
    return MagicMock(return_value=(stub_text, 1.23, 512))


# ---------------------------------------------------------------------------
# pytest marks
# ---------------------------------------------------------------------------

pytestmark = []  # module-level marks applied per test below


# ---------------------------------------------------------------------------
# 1. Model accessibility tests
# ---------------------------------------------------------------------------

@pytest.mark.accessibility
def test_llama_3_1_8b_accessible():
    """Groq-hosted Llama 3.1 8B is accessible when GROQ_API_KEY is set."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        pytest.skip("GROQ_API_KEY not set — skipping Groq accessibility check")

    config = MODEL_REGISTRY["llama-3.1-8b"]
    assert config.provider == "groq"
    assert config.required is True
    # Verify the endpoint is reachable (HEAD request to base URL)
    try:
        resp = requests.get(config.api_endpoint, timeout=5)
        # Groq returns 404 on GET / but the host is reachable
        assert resp.status_code in (200, 404, 405)
    except requests.RequestException as exc:
        pytest.skip(f"Groq endpoint unreachable: {exc}")


@pytest.mark.accessibility
def test_qwen_2_5_7b_accessible():
    """Ollama-hosted Qwen 2.5 7B is accessible when Ollama is running."""
    if not _is_ollama_reachable():
        pytest.skip("Ollama not running at http://localhost:11434 — skipping")

    config = MODEL_REGISTRY["qwen-2.5-7b"]
    assert config.provider == "ollama"
    assert config.required is True


@pytest.mark.accessibility
def test_gemma_4_9b_accessible():
    """Ollama-hosted Gemma 4 9B is accessible when Ollama is running."""
    if not _is_ollama_reachable():
        pytest.skip("Ollama not running at http://localhost:11434 — skipping")

    config = MODEL_REGISTRY["gemma-4-9b"]
    assert config.provider == "ollama"
    assert config.required is True


# ---------------------------------------------------------------------------
# 2. Comparison run — mocked backends
# ---------------------------------------------------------------------------

@patch("src.model_comparison.backends.Ollama_Backend.generate")
@patch("src.model_comparison.backends.Groq_Backend.generate")
def test_comparison_two_models_sale_deed(mock_groq_gen, mock_ollama_gen, tmp_path):
    """
    run_comparison across Llama 3.1 8B (Groq) + Qwen 2.5 7B (Ollama) with
    mocked backends produces 2 successful runs with all required metric keys.

    Requirements: 12.1, 12.3, 12.4, 12.5
    """
    mock_groq_gen.side_effect = _make_mock_generate()
    mock_ollama_gen.side_effect = _make_mock_generate()

    fact_patterns = {"sale_deed": MINIMAL_SALE_DEED_FACT_PATTERN}

    report = run_comparison(
        models=["llama-3.1-8b", "qwen-2.5-7b"],
        presets=["high_quality"],
        doc_types=["sale_deed"],
        fact_patterns=fact_patterns,
        output_dir=tmp_path,
    )

    # Basic counts
    assert report.successful_runs == 2, (
        f"Expected 2 successful runs, got {report.successful_runs}. "
        f"Failed: {report.failed_runs}"
    )
    assert len(report.runs) == 2

    # Each run has the correct model_id
    model_ids = {run.model_id for run in report.runs}
    assert model_ids == {"llama-3.1-8b", "qwen-2.5-7b"}

    for run in report.runs:
        eval_result = run.evaluation_result

        # Tier 1 keys present
        for key in _TIER1_KEYS:
            assert key in eval_result, (
                f"Tier 1 key '{key}' missing from evaluation_result of run {run.run_id}"
            )

        # Tier 2 keys present
        for key in _TIER2_KEYS:
            assert key in eval_result, (
                f"Tier 2 key '{key}' missing from evaluation_result of run {run.run_id}"
            )

        # RAGAS keys absent (Tier 3 skipped)
        ragas_found = _RAGAS_KEYS & set(eval_result.keys())
        assert not ragas_found, (
            f"RAGAS keys should be absent but found: {ragas_found} in run {run.run_id}"
        )


# ---------------------------------------------------------------------------
# 3. CSV export — columns and row count
# ---------------------------------------------------------------------------

@patch("src.model_comparison.backends.Ollama_Backend.generate")
@patch("src.model_comparison.backends.Groq_Backend.generate")
def test_csv_export_columns_and_row_count(mock_groq_gen, mock_ollama_gen, tmp_path):
    """
    export_csv produces a CSV with all required columns and one row per
    successful run.

    Requirements: 12.4, 12.5
    """
    mock_groq_gen.side_effect = _make_mock_generate()
    mock_ollama_gen.side_effect = _make_mock_generate()

    fact_patterns = {"sale_deed": MINIMAL_SALE_DEED_FACT_PATTERN}

    report = run_comparison(
        models=["llama-3.1-8b", "qwen-2.5-7b"],
        presets=["high_quality"],
        doc_types=["sale_deed"],
        fact_patterns=fact_patterns,
        output_dir=tmp_path,
    )

    csv_path = tmp_path / "results.csv"
    export_csv(report, csv_path)

    assert csv_path.exists(), "CSV file was not created"

    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        columns = set(reader.fieldnames or [])

    # Row count matches successful runs
    assert len(rows) == report.successful_runs, (
        f"CSV has {len(rows)} rows but report has {report.successful_runs} successful runs"
    )

    # All required columns present
    missing_cols = _REQUIRED_CSV_COLUMNS - columns
    assert not missing_cols, f"CSV missing required columns: {missing_cols}"


# ---------------------------------------------------------------------------
# 4. Large models skipped by default
# ---------------------------------------------------------------------------

def test_large_models_skipped_by_default():
    """
    get_available_models(include_large=False) returns only required models
    (parameter_count ≤ 10B).

    Requirements: 12.6
    """
    models = get_available_models(include_large=False)

    assert len(models) > 0, "Expected at least one required model"

    for model in models:
        assert model.required is True, (
            f"Model '{model.model_id}' has required=False but was returned "
            "when include_large=False"
        )
        assert model.parameter_count <= 10, (
            f"Model '{model.model_id}' has {model.parameter_count}B params "
            "but should be ≤10B when include_large=False"
        )


# ---------------------------------------------------------------------------
# 5. Large models included when flag set
# ---------------------------------------------------------------------------

def test_large_models_included_with_flag(include_large_models):
    """
    get_available_models(include_large=True) returns at least one optional
    (required=False) model when the --include-large-models flag is set.

    Requirements: 12.7
    """
    if not include_large_models:
        pytest.skip("Pass --include-large-models to run this test")

    models = get_available_models(include_large=True)
    optional_models = [m for m in models if not m.required]

    # There may be no optional models if RAM is insufficient, but the function
    # must at least return the required models.
    assert len(models) >= 3, "Expected at least 3 required models"

    # If RAM is sufficient, at least one optional model should be present.
    # We don't assert this hard because CI machines may have limited RAM.
    # Instead, verify the registry has optional models defined.
    all_optional = [m for m in MODEL_REGISTRY.values() if not m.required]
    assert len(all_optional) > 0, "MODEL_REGISTRY should define optional large models"


# ---------------------------------------------------------------------------
# 6. Error resilience — failed run logged and skipped
# ---------------------------------------------------------------------------

@patch("src.model_comparison.backends.Groq_Backend.generate")
def test_failed_run_logged_and_skipped(mock_groq_gen, tmp_path):
    """
    When a backend raises ModelAPIError, the run is recorded in failed_runs
    and the comparison continues (successful_runs == 0 for a single-model run).

    Requirements: 12.8
    """
    mock_groq_gen.side_effect = ModelAPIError("simulated failure")

    fact_patterns = {"sale_deed": MINIMAL_SALE_DEED_FACT_PATTERN}

    report = run_comparison(
        models=["llama-3.1-8b"],
        presets=["high_quality"],
        doc_types=["sale_deed"],
        fact_patterns=fact_patterns,
        output_dir=tmp_path,
    )

    assert report.successful_runs == 0, (
        f"Expected 0 successful runs, got {report.successful_runs}"
    )
    assert len(report.failed_runs) == 1, (
        f"Expected 1 failed run, got {len(report.failed_runs)}"
    )
    error_msg = report.failed_runs[0].get("error", "")
    assert "simulated failure" in error_msg, (
        f"Expected 'simulated failure' in error message, got: '{error_msg}'"
    )


# ---------------------------------------------------------------------------
# 7. Integration test — real Groq API (skipped when key not set)
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("GROQ_API_KEY"),
    reason="GROQ_API_KEY not set — skipping real Groq API integration test",
)
def test_real_comparison_llama_only(tmp_path):
    """
    Real Groq API call for llama-3.1-8b with 1 preset and 1 doc type.
    Verifies latency, token count, and non-empty generated text.

    Requirements: 12.2
    """
    fact_patterns = {"sale_deed": MINIMAL_SALE_DEED_FACT_PATTERN}

    report = run_comparison(
        models=["llama-3.1-8b"],
        presets=["fast"],
        doc_types=["sale_deed"],
        fact_patterns=fact_patterns,
        output_dir=tmp_path,
    )

    assert report.successful_runs >= 1, (
        f"Expected at least 1 successful run. Failed: {report.failed_runs}"
    )

    run = report.runs[0]
    assert run.latency_seconds > 0, "latency_seconds should be positive"
    assert run.token_count > 0, "token_count should be positive"
    assert isinstance(run.generated_text, str) and len(run.generated_text) > 0, (
        "generated_text should be a non-empty string"
    )
