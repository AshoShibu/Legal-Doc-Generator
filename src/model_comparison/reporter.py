"""
src/model_comparison/reporter.py

Comparison_Reporter — exports model comparison results in CSV, JSON, and
Markdown formats suitable for statistical analysis and research paper tables.

Output files written to output_dir:
  results.csv       — one row per run, all metrics
  summary.csv       — one row per model-preset combo, mean ± std per metric
  results.json      — nested {model_id: {preset: {doc_type: [runs]}}}
  summary.md        — Markdown report with comparison tables and recommendations
"""
from __future__ import annotations

import csv
import json
import logging
import statistics
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# CSV columns in required order (Requirements 18.2)
_DETAIL_COLUMNS = [
    "timestamp",
    "model_id",
    "parameter_preset",
    "doc_type",
    "run_id",
    "backend",
    "latency_seconds",
    "token_count",
    # Tier 1
    "cache_hit_rate",
    "slot_fill_rate",
    "fact_fidelity_score",
    # Tier 2
    "section_completeness",
    "citation_format_compliance",
    "jurisdictional_accuracy",
    # Tier 3 RAGAS (present only when evaluated)
    "ragas_faithfulness",
    "ragas_answer_relevancy",
    "ragas_llm_context_precision_with_reference",
    "ragas_context_recall",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _flat_run(run) -> dict[str, Any]:
    """Flatten a ComparisonRun into a single dict for CSV/JSON export."""
    base = {
        "timestamp": run.timestamp,
        "model_id": run.model_id,
        "parameter_preset": run.parameter_preset,
        "doc_type": run.doc_type,
        "run_id": run.run_id,
        "backend": run.backend,
        "latency_seconds": run.latency_seconds,
        "token_count": run.token_count,
    }
    # Merge evaluation metrics (may be empty dict if evaluation failed)
    eval_metrics = {
        k: v for k, v in run.evaluation_result.items()
        if k not in ("run_id", "doc_type", "pipeline_variant")
    }
    base.update(eval_metrics)
    return base


def _row_for_columns(flat: dict, columns: list[str]) -> dict[str, Any]:
    """Return an ordered dict with exactly the given columns (None for missing)."""
    return {col: flat.get(col, None) for col in columns}


def _collect_metric_keys(runs) -> list[str]:
    """Collect all numeric metric keys that appear in any run's evaluation_result."""
    keys: list[str] = []
    seen: set[str] = set()
    for run in runs:
        for k, v in run.evaluation_result.items():
            if k not in seen and isinstance(v, (int, float)) and k not in ("run_id",):
                keys.append(k)
                seen.add(k)
    return keys


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def export_csv(report, output_path: Path, deduplicate: bool = True) -> None:
    """Export detailed results as CSV — one row per run.

    When deduplicate=True (default), rewrites the file with unique rows keyed
    on (model_id, parameter_preset, doc_type, run_id), preventing duplicate
    rows when the same combination is re-run.

    Args:
        report:       ComparisonReport from run_comparison().
        output_path:  Destination .csv path.
        deduplicate:  Remove duplicate rows (same run_id). Default True.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Determine full column set
    extra_metrics = [
        k for k in _collect_metric_keys(report.runs)
        if k not in _DETAIL_COLUMNS
    ]
    columns = _DETAIL_COLUMNS + extra_metrics

    # Load existing rows if deduplicating
    existing_rows: dict[str, dict] = {}  # run_id → row
    if deduplicate and output_path.exists():
        try:
            with open(output_path, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    run_id = row.get("run_id", "")
                    if run_id:
                        existing_rows[run_id] = row
        except Exception:
            pass  # corrupt file — overwrite

    # Merge new runs (new rows override existing with same run_id)
    for run in report.runs:
        flat = _flat_run(run)
        existing_rows[run.run_id] = _row_for_columns(flat, columns)

    # Write merged result — use only the defined columns (no extras from old rows)
    all_rows = [_row_for_columns(row, columns) for row in existing_rows.values()]
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    action = "written" if not deduplicate else f"written ({len(all_rows)} unique rows)"
    logger.info("Exported %d runs to %s (%s)", len(report.runs), output_path, action)


def export_summary_csv(report, output_path: Path) -> None:
    """Export summary statistics — one row per model-preset combination.

    Columns: model_id, parameter_preset, then mean_<metric> and std_<metric>
    for every numeric metric.

    Args:
        report:      ComparisonReport from run_comparison().
        output_path: Destination .csv path.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    metric_keys = _collect_metric_keys(report.runs)

    # Build summary rows from report.summary
    rows: list[dict] = []
    for combo_key, metrics in report.summary.items():
        model_id, preset = combo_key.rsplit("/", 1)
        row: dict[str, Any] = {"model_id": model_id, "parameter_preset": preset}
        for metric in metric_keys:
            stats = metrics.get(metric, {})
            row[f"mean_{metric}"] = stats.get("mean", None)
            row[f"std_{metric}"] = stats.get("std", None)
            row[f"median_{metric}"] = stats.get("median", None)
        rows.append(row)

    if not rows:
        logger.warning("No summary rows to export — report may be empty.")
        return

    fieldnames = list(rows[0].keys())
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    logger.info("Exported summary CSV (%d combos) to %s", len(rows), output_path)


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------

def export_json(report, output_path: Path) -> None:
    """Export full report as nested JSON.

    Structure: {model_id: {parameter_preset: {doc_type: [run_dicts]}}}
    Also includes top-level "summary", "best_models", and "failed_runs" keys.

    Args:
        report:      ComparisonReport from run_comparison().
        output_path: Destination .json path.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build nested run structure
    nested: dict[str, dict[str, dict[str, list]]] = {}
    for run in report.runs:
        nested \
            .setdefault(run.model_id, {}) \
            .setdefault(run.parameter_preset, {}) \
            .setdefault(run.doc_type, []) \
            .append(_flat_run(run))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_runs": report.total_runs,
        "successful_runs": report.successful_runs,
        "total_duration_seconds": report.total_duration_seconds,
        "runs": nested,
        "summary": report.summary,
        "best_models": report.best_models,
        "failed_runs": report.failed_runs,
    }

    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    logger.info("Exported JSON report to %s", output_path)


# ---------------------------------------------------------------------------
# Aggregate statistics (also used by Markdown report)
# ---------------------------------------------------------------------------

def compute_aggregate_stats(runs) -> dict[str, dict[str, dict[str, float]]]:
    """Compute mean, median, std per model-preset combination per metric.

    Returns:
        {combo_key: {metric: {"mean": ..., "median": ..., "std": ..., "n": ...}}}
    """
    groups: dict[str, list[dict]] = {}
    for run in runs:
        key = f"{run.model_id}/{run.parameter_preset}"
        groups.setdefault(key, []).append(run.evaluation_result)

    result: dict[str, dict[str, dict[str, float]]] = {}
    for combo_key, eval_list in groups.items():
        metric_keys = _collect_metric_keys_from_dicts(eval_list)
        result[combo_key] = {}
        for metric in metric_keys:
            values = [e[metric] for e in eval_list if metric in e and e[metric] is not None]
            if not values:
                continue
            result[combo_key][metric] = {
                "mean": round(statistics.mean(values), 4),
                "median": round(statistics.median(values), 4),
                "std": round(statistics.stdev(values) if len(values) > 1 else 0.0, 4),
                "n": len(values),
            }
    return result


def _collect_metric_keys_from_dicts(eval_list: list[dict]) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for e in eval_list:
        for k, v in e.items():
            if k not in seen and isinstance(v, (int, float)) and k not in ("run_id",):
                keys.append(k)
                seen.add(k)
    return keys


def identify_best_models(summary: dict[str, Any]) -> dict[str, str]:
    """Return the best model-preset combo per metric (highest mean).

    Args:
        summary: Output of compute_aggregate_stats() or report.summary.

    Returns:
        {metric_name: best_combo_key}
    """
    best: dict[str, tuple[str, float]] = {}
    for combo_key, metrics in summary.items():
        for metric, stats in metrics.items():
            mean = stats.get("mean", 0.0)
            if metric not in best or mean > best[metric][1]:
                best[metric] = (combo_key, mean)
    return {metric: combo for metric, (combo, _) in best.items()}


# ---------------------------------------------------------------------------
# Markdown summary report
# ---------------------------------------------------------------------------

def generate_summary_markdown(report, output_path: Path) -> None:
    """Generate a Markdown report with comparison tables and recommendations.

    Sections:
      - Executive summary (best model per metric)
      - Per-metric comparison tables (mean ± std)
      - Latency comparison
      - Failed runs
      - Recommendations

    Args:
        report:      ComparisonReport from run_comparison().
        output_path: Destination .md path.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    summary = report.summary or compute_aggregate_stats(report.runs)
    best = report.best_models or identify_best_models(summary)

    lines: list[str] = []

    # Header
    lines += [
        "# Model Comparison Report",
        "",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
        "",
        f"**Total runs:** {report.total_runs} | "
        f"**Successful:** {report.successful_runs} | "
        f"**Failed:** {len(report.failed_runs)} | "
        f"**Duration:** {report.total_duration_seconds:.1f}s",
        "",
    ]

    # Executive summary table
    lines += [
        "## Executive Summary",
        "",
        "| Metric | Best Model / Preset | Mean Score |",
        "|---|---|---|",
    ]
    for metric, combo in sorted(best.items()):
        mean_val = summary.get(combo, {}).get(metric, {}).get("mean", "—")
        mean_str = f"{mean_val:.4f}" if isinstance(mean_val, float) else str(mean_val)
        lines.append(f"| {metric} | {combo} | {mean_str} |")
    lines.append("")

    # Per-metric comparison tables
    combo_keys = sorted(summary.keys())
    if not combo_keys:
        lines += ["*No successful runs to compare.*", ""]
    else:
        # Collect all metrics across all combos
        all_metrics: list[str] = []
        seen_m: set[str] = set()
        for combo in combo_keys:
            for m in summary[combo]:
                if m not in seen_m:
                    all_metrics.append(m)
                    seen_m.add(m)

        # Group metrics by category for readability
        tier1 = [m for m in all_metrics if m in (
            "cache_hit_rate", "slot_fill_rate", "fact_fidelity_score", "latency_seconds"
        )]
        tier2 = [m for m in all_metrics if m in (
            "section_completeness", "citation_format_compliance", "jurisdictional_accuracy"
        )]
        tier3 = [m for m in all_metrics if m.startswith("ragas_")]
        other = [m for m in all_metrics if m not in tier1 + tier2 + tier3]

        def _metric_table(metrics: list[str], title: str) -> None:
            if not metrics:
                return
            lines.append(f"## {title}")
            lines.append("")
            header = "| Model / Preset | " + " | ".join(metrics) + " |"
            sep = "|---|" + "---|" * len(metrics)
            lines.extend([header, sep])
            for combo in combo_keys:
                cells = []
                for m in metrics:
                    stats = summary.get(combo, {}).get(m, {})
                    if stats:
                        cells.append(f"{stats['mean']:.4f} ± {stats['std']:.4f}")
                    else:
                        cells.append("—")
                lines.append(f"| {combo} | " + " | ".join(cells) + " |")
            lines.append("")

        _metric_table(tier1, "Tier 1 Metrics (CAG-Specific)")
        _metric_table(tier2, "Tier 2 Metrics (Structural / Legal)")
        if tier3:
            _metric_table(tier3, "Tier 3 Metrics (RAGAS)")
        if other:
            _metric_table(other, "Other Metrics")

    # Latency comparison
    lines += ["## Latency Comparison", ""]
    lines += [
        "| Model / Preset | Mean Latency (s) | Median (s) | P95 est. |",
        "|---|---|---|---|",
    ]
    for combo in combo_keys:
        lat = summary.get(combo, {}).get("latency_seconds", {})
        if lat:
            # Rough P95 estimate: mean + 1.65 * std
            p95 = lat["mean"] + 1.65 * lat["std"]
            lines.append(
                f"| {combo} | {lat['mean']:.2f} | {lat['median']:.2f} | {p95:.2f} |"
            )
        else:
            lines.append(f"| {combo} | — | — | — |")
    lines.append("")

    # Failed runs
    if report.failed_runs:
        lines += ["## Failed Runs", ""]
        for f in report.failed_runs:
            lines.append(
                f"- **{f.get('model', '?')}** / {f.get('preset', '?')} / "
                f"{f.get('doc_type', '?')}: {f.get('error', 'unknown error')}"
            )
        lines.append("")
    else:
        lines += ["## Failed Runs", "", "*No failed runs.*", ""]

    # Recommendations
    lines += ["## Recommendations", ""]
    groq_combos = [c for c in combo_keys if "groq" in c.lower() or "llama" in c.lower()]
    ollama_combos = [c for c in combo_keys if c not in groq_combos]

    if best:
        # Best overall quality = highest mean across Tier 1+2 metrics
        quality_metrics = [
            m for m in ("slot_fill_rate", "fact_fidelity_score", "section_completeness")
            if m in best
        ]
        if quality_metrics:
            # Pick the combo that wins the most quality metrics
            from collections import Counter
            quality_winner = Counter(best[m] for m in quality_metrics).most_common(1)[0][0]
            lines.append(f"- **Best overall quality**: `{quality_winner}`")

        if "latency_seconds" in best:
            lines.append(f"- **Lowest latency**: `{best['latency_seconds']}`")

        if ollama_combos:
            # Best local inference = best slot_fill_rate among Ollama combos
            best_local = max(
                ollama_combos,
                key=lambda c: summary.get(c, {}).get("slot_fill_rate", {}).get("mean", 0),
            )
            lines.append(f"- **Best local inference (Ollama)**: `{best_local}`")

    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Generated Markdown summary report: %s", output_path)
