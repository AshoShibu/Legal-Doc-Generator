"""
src/model_comparison/comparator.py

Model_Comparator — orchestrates document generation across multiple
model-parameter-document-type combinations and collects evaluation metrics.

Execution model
---------------
- Runs combinations sequentially to avoid RAM contention with Ollama models.
  Ollama loads one model at a time anyway, so parallelism adds overhead without
  benefit. Groq calls are fast enough (~3-5s) that sequential is fine.
- Writes intermediate JSON results to disk after each (model, preset, doc_type)
  completes so that a crash does not lose all progress.
- Integrates Tier 1+2 evaluation via ``evaluate_cag_document()``.
- Integrates Tier 3 RAGAS evaluation when ``GROQ_RAGAS_API_KEY`` is set.
- Failed runs are logged and skipped; remaining combinations continue.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tqdm import tqdm

from src.model_comparison.model_registry import (
    MODEL_REGISTRY,
    PARAMETER_PRESETS,
    ModelConfig,
    ParameterPreset,
    get_backend,
)
from src.model_comparison.backends import ModelAPIError, ModelTimeoutError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ComparisonRun:
    """Result of one (model, preset, doc_type) generation + evaluation run."""
    run_id: str
    timestamp: str                      # ISO-8601 UTC
    model_id: str
    parameter_preset: str
    doc_type: str
    fact_pattern: dict
    generated_text: str
    evaluation_result: dict             # CAGEvaluationResult.summary() or {}
    latency_seconds: float
    token_count: int
    backend: str                        # "groq" | "ollama"


@dataclass
class ComparisonReport:
    """Aggregated results from a full comparison run."""
    runs: list[ComparisonRun] = field(default_factory=list)
    failed_runs: list[dict[str, str]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    best_models: dict[str, str] = field(default_factory=dict)
    total_runs: int = 0
    successful_runs: int = 0
    total_duration_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_generated_document(
    text: str,
    run_id: str,
    doc_type: str,
    model_id: str,
):
    """
    Wrap raw LLM text in a minimal GeneratedDocument-compatible object so
    that ``evaluate_cag_document()`` can consume it without requiring the
    full CAG pipeline.
    """
    from src.generation.document_generator import GeneratedDocument

    return GeneratedDocument(
        content=text,
        header="",
        body=text,
        citation_index="",
        citations=[],
        ungrounded_clauses=[],
        high_hallucination_risk=False,
        run_id=run_id,
        unfilled_slots=[],
        doc_type=doc_type,
        pipeline_variant=f"model_comparison/{model_id}",
    )


def _evaluate(
    generated_text: str,
    run_id: str,
    doc_type: str,
    model_id: str,
    fact_pattern: dict,
    latency_seconds: float,
) -> dict:
    """
    Run Tier 1+2 evaluation and optionally Tier 3 RAGAS.

    Returns a flat dict (``CAGEvaluationResult.summary()``).
    Returns ``{}`` on any evaluation failure (non-fatal).
    """
    try:
        from src.evaluation.evaluator import evaluate_cag_document, evaluate_ragas
    except ImportError as exc:
        logger.warning("Evaluator not available — skipping evaluation: %s", exc)
        return {}

    try:
        doc = _make_generated_document(generated_text, run_id, doc_type, model_id)
        result = evaluate_cag_document(
            doc=doc,
            fact_pattern=fact_pattern,
            template_slots=None,
            latency_seconds=latency_seconds,
        )

        # Tier 3 RAGAS — only when API key is present
        ragas_key = os.environ.get("GROQ_RAGAS_API_KEY") or os.environ.get("GROQ_API_KEY")
        if ragas_key:
            try:
                # RAGAS needs a cache object with .documents; supply an empty stub
                # since model comparison runs outside the CAG cache pipeline.
                class _EmptyCache:
                    documents: list = []

                ragas_metrics = evaluate_ragas(doc, _EmptyCache(), fact_pattern)
                result.ragas = ragas_metrics
            except Exception as ragas_exc:
                logger.warning(
                    "RAGAS evaluation failed for run_id=%s: %s", run_id, ragas_exc
                )
        else:
            logger.debug(
                "GROQ_RAGAS_API_KEY not set — skipping Tier 3 RAGAS for run_id=%s",
                run_id,
            )

        return result.summary()

    except Exception as exc:
        logger.warning(
            "Evaluation failed for run_id=%s (doc_type=%s, model=%s): %s",
            run_id, doc_type, model_id, exc,
        )
        return {}


def _write_intermediate(run: ComparisonRun, output_dir: Path) -> None:
    """Persist a single run result to disk immediately after completion."""
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_model = run.model_id.replace("/", "_").replace(":", "_")
    filename = f"{safe_model}__{run.parameter_preset}__{run.doc_type}__{run.run_id[:8]}.json"
    path = output_dir / "intermediate" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(asdict(run), fh, indent=2, ensure_ascii=False)
    logger.debug("Intermediate result written: %s", path)


def _run_single(
    model_key: str,
    preset_name: str,
    doc_type: str,
    fact_pattern: dict,
    output_dir: Path,
) -> ComparisonRun | dict:
    """
    Execute one (model, preset, doc_type) combination.

    Returns a :class:`ComparisonRun` on success, or a ``dict`` describing
    the failure (to be appended to ``failed_runs``).
    """
    model_config: ModelConfig = MODEL_REGISTRY[model_key]
    preset: ParameterPreset = PARAMETER_PRESETS[preset_name]
    run_id = str(uuid.uuid4())

    # Serialise fact pattern to a plain-text prompt
    fact_text = "\n".join(
        f"{k}: {v}" for k, v in fact_pattern.items() if v not in (None, "", [])
    )

    logger.info(
        "Starting run: model=%s preset=%s doc_type=%s run_id=%s",
        model_key, preset_name, doc_type, run_id,
    )

    try:
        backend = get_backend(model_key)
        document_text, latency, token_count = backend.generate(
            fact_pattern=fact_text,
            model_config=model_config,
            preset=preset,
        )
    except ModelTimeoutError as exc:
        msg = f"Timeout: {exc}"
        logger.warning("SKIP %s/%s/%s — %s", model_key, preset_name, doc_type, msg)
        return {"model": model_key, "preset": preset_name, "doc_type": doc_type, "error": msg}
    except ModelAPIError as exc:
        msg = f"API error: {exc}"
        logger.warning("SKIP %s/%s/%s — %s", model_key, preset_name, doc_type, msg)
        return {"model": model_key, "preset": preset_name, "doc_type": doc_type, "error": msg}
    except Exception as exc:
        msg = f"Unexpected error: {exc}"
        logger.error("SKIP %s/%s/%s — %s", model_key, preset_name, doc_type, msg, exc_info=True)
        return {"model": model_key, "preset": preset_name, "doc_type": doc_type, "error": msg}

    evaluation = _evaluate(
        generated_text=document_text,
        run_id=run_id,
        doc_type=doc_type,
        model_id=model_key,
        fact_pattern=fact_pattern,
        latency_seconds=latency,
    )

    run = ComparisonRun(
        run_id=run_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        model_id=model_key,
        parameter_preset=preset_name,
        doc_type=doc_type,
        fact_pattern=fact_pattern,
        generated_text=document_text,
        evaluation_result=evaluation,
        latency_seconds=latency,
        token_count=token_count,
        backend=model_config.provider,
    )

    _write_intermediate(run, output_dir)
    return run


# ---------------------------------------------------------------------------
# Aggregate statistics helpers
# ---------------------------------------------------------------------------

def _compute_summary(runs: list[ComparisonRun]) -> dict[str, Any]:
    """Compute mean/median/std per model-preset combination per metric."""
    import statistics

    # Group by (model_id, parameter_preset)
    groups: dict[tuple[str, str], list[dict]] = {}
    for run in runs:
        key = (run.model_id, run.parameter_preset)
        groups.setdefault(key, []).append(run.evaluation_result)

    summary: dict[str, Any] = {}
    for (model_id, preset), eval_list in groups.items():
        # Collect numeric metric keys from the first non-empty result
        metric_keys = [
            k for k, v in (eval_list[0] if eval_list else {}).items()
            if isinstance(v, (int, float)) and k not in ("grounded_citations", "ungrounded_citations")
        ]
        combo_key = f"{model_id}/{preset}"
        summary[combo_key] = {}
        for metric in metric_keys:
            values = [e[metric] for e in eval_list if metric in e and e[metric] is not None]
            if not values:
                continue
            summary[combo_key][metric] = {
                "mean": round(statistics.mean(values), 4),
                "median": round(statistics.median(values), 4),
                "std": round(statistics.stdev(values) if len(values) > 1 else 0.0, 4),
                "min": round(min(values), 4),
                "max": round(max(values), 4),
                "n": len(values),
            }
    return summary


def _identify_best_models(summary: dict[str, Any]) -> dict[str, str]:
    """Return the best model-preset combo per metric (highest mean)."""
    best: dict[str, tuple[str, float]] = {}  # metric → (combo_key, mean)
    for combo_key, metrics in summary.items():
        for metric, stats in metrics.items():
            mean = stats.get("mean", 0.0)
            if metric not in best or mean > best[metric][1]:
                best[metric] = (combo_key, mean)
    return {metric: combo for metric, (combo, _) in best.items()}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_comparison(
    models: list[str],
    presets: list[str],
    doc_types: list[str],
    fact_patterns: dict[str, dict],
    output_dir: Path,
    skip_combinations: set[tuple[str, str, str]] | None = None,
    prior_runs: list[ComparisonRun] | None = None,
) -> ComparisonReport:
    """Execute document generation for all (model, preset, doc_type) combinations.

    Args:
        models:             List of model keys from :data:`MODEL_REGISTRY`.
        presets:            List of preset names from :data:`PARAMETER_PRESETS`.
        doc_types:          List of document type strings.
        fact_patterns:      Mapping of ``doc_type → fact_pattern dict``.
        output_dir:         Directory where intermediate and final results are written.
        skip_combinations:  Set of (model_id, preset, doc_type) tuples to skip (resume mode).
        prior_runs:         ComparisonRun objects loaded from a previous interrupted run.

    Returns:
        :class:`ComparisonReport` with all results, summary statistics, and
        best-model identification.
    """
    import time as _time

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    skip = skip_combinations or set()

    # Build the full work list, filtering out already-completed combinations
    work_items = [
        (model_key, preset_name, doc_type)
        for model_key in models
        for preset_name in presets
        for doc_type in doc_types
        if (model_key, preset_name, doc_type) not in skip
    ]
    total_planned = len(models) * len(presets) * len(doc_types)
    skipped_count = total_planned - len(work_items)

    logger.info(
        "Starting model comparison: %d models × %d presets × %d doc_types = %d runs"
        "%s",
        len(models), len(presets), len(doc_types), total_planned,
        f" ({skipped_count} skipped — resume mode)" if skipped_count else "",
    )

    # Seed report with prior runs if resuming
    report = ComparisonReport(total_runs=total_planned)
    if prior_runs:
        report.runs.extend(prior_runs)
        report.successful_runs = len(prior_runs)
        logger.info("Resume: loaded %d prior successful runs", len(prior_runs))

    t_start = _time.monotonic()
    _last_groq_model: str | None = None

    with tqdm(total=len(work_items), desc="Model Comparison", unit="run") as pbar:
        for model_key, preset_name, doc_type in work_items:
            fact_pattern = fact_patterns.get(doc_type, {})
            pbar.set_postfix(model=model_key, doc_type=doc_type)

            from src.model_comparison.model_registry import MODEL_REGISTRY
            cfg = MODEL_REGISTRY[model_key]
            if cfg.provider == "groq" and _last_groq_model == model_key:
                cooldown = 8 if cfg.parameter_count >= 70 else 3
                _time.sleep(cooldown)
            _last_groq_model = model_key if cfg.provider == "groq" else _last_groq_model

            result = _run_single(
                model_key,
                preset_name,
                doc_type,
                fact_pattern,
                output_dir,
            )

            if isinstance(result, ComparisonRun):
                report.runs.append(result)
                report.successful_runs += 1
            else:
                report.failed_runs.append(result)

            pbar.update(1)

    report.total_duration_seconds = round(_time.monotonic() - t_start, 2)
    report.summary = _compute_summary(report.runs)
    report.best_models = _identify_best_models(report.summary)

    logger.info(
        "Comparison complete: %d/%d successful, %d failed, %.1fs total",
        report.successful_runs, total_planned, len(report.failed_runs),
        report.total_duration_seconds,
    )

    return report
