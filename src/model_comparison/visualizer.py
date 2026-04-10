"""
src/model_comparison/visualizer.py

Research-paper-quality visualizations for the 3-tier SLM model comparison.

Generates 5 figures saved to output_dir/visuals/:
  1. quality_heatmap.png      — metric heatmap across all model/preset combos
  2. latency_comparison.png   — latency bar chart grouped by tier
  3. quality_radar.png        — radar/spider chart per tier (mean of quality metrics)
  4. metric_bars.png          — grouped bar chart for each quality metric by tier
  5. fact_fidelity_dist.png   — distribution of fact_fidelity_score per model

All figures use a clean academic style suitable for inclusion in a research paper.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")  # non-interactive backend — safe for servers and Docker
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd
import seaborn as sns

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Style constants
# ---------------------------------------------------------------------------

# Tier colour palette — distinct, print-friendly, colourblind-safe
_TIER_PALETTE = {
    "A-Baseline":   "#2166AC",   # blue
    "B-Small":      "#D6604D",   # red-orange
    "C-LargeCloud": "#4DAC26",   # green
    "optional":     "#888888",   # grey
}

_TIER_LABELS = {
    "A-Baseline":   "Tier A: Baseline (Groq)",
    "B-Small":      "Tier B: Small Local",
    "C-LargeCloud": "Tier C: Large Cloud",
    "optional":     "Optional",
}

# Models belonging to each tier
_TIER_MAP = {
    "llama-3.1-8b":        "A-Baseline",
    "llama-3.3-70b":       "A-Baseline",
    "gemma-4-e4b":         "B-Small",
    "qwen-3.5-4b":         "B-Small",
    "gemma-4-31b-cloud":   "C-LargeCloud",
    "qwen-3.5-397b-cloud": "C-LargeCloud",
}

# Quality metrics to include in most plots
_QUALITY_METRICS = [
    "fact_fidelity_score",
    "section_completeness",
    "slot_fill_rate",
    "cache_hit_rate",
    "citation_format_compliance",
    "jurisdictional_accuracy",
]

_METRIC_LABELS = {
    "fact_fidelity_score":        "Fact Fidelity",
    "section_completeness":       "Section Completeness",
    "slot_fill_rate":             "Slot Fill Rate",
    "cache_hit_rate":             "Cache Hit Rate",
    "citation_format_compliance": "Citation Compliance",
    "jurisdictional_accuracy":    "Jurisdictional Accuracy",
    "latency_seconds":            "Latency (s)",
}

# Short model display names for axis labels
_MODEL_SHORT = {
    "llama-3.1-8b":        "Llama 3.1 8B",
    "llama-3.3-70b":       "Llama 3.3 70B",
    "gemma-4-e4b":         "Gemma 4 E4B",
    "qwen-3.5-4b":         "Qwen 3.5 4B",
    "gemma-4-31b-cloud":   "Gemma 4 31B\nCloud",
    "qwen-3.5-397b-cloud": "Qwen 3.5 397B\nMoE Cloud",
}


def _apply_paper_style() -> None:
    """Apply a clean, publication-ready matplotlib style."""
    sns.set_theme(style="whitegrid", context="paper", font_scale=1.1)
    plt.rcParams.update({
        "figure.dpi": 150,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
        "font.family": "sans-serif",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.titleweight": "bold",
        "axes.titlesize": 12,
        "axes.labelsize": 10,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "legend.fontsize": 9,
        "legend.framealpha": 0.9,
    })


def _load_results(results_csv: Path) -> pd.DataFrame:
    """Load and clean results CSV, keeping only the latest run per (model, preset, doc_type)."""
    # Use engine='python' and dtype=str for key columns to avoid pyarrow inference issues
    df = pd.read_csv(results_csv, dtype={"model_id": str, "parameter_preset": str,
                                          "doc_type": str, "run_id": str, "backend": str},
                     engine="python")

    # Coerce timestamp
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601", errors="coerce")
        if df["timestamp"].isna().all():
            df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.sort_values("timestamp")

    # Deduplicate: keep the most recent run per (model_id, parameter_preset, doc_type)
    dedup_cols = [c for c in ["model_id", "parameter_preset", "doc_type"] if c in df.columns]
    if dedup_cols:
        df = df.drop_duplicates(subset=dedup_cols, keep="last").reset_index(drop=True)

    # Add tier column
    df["tier"] = df["model_id"].map(_TIER_MAP).fillna("optional")

    # Add short model name
    df["model_short"] = df["model_id"].map(_MODEL_SHORT).fillna(df["model_id"])

    # Combo label for axes
    df["combo"] = df["model_short"] + "\n(" + df["parameter_preset"] + ")"

    # Coerce metric columns to numeric
    for col in _QUALITY_METRICS + ["latency_seconds"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def _tier_color(tier: str) -> str:
    return _TIER_PALETTE.get(tier, "#888888")


# ---------------------------------------------------------------------------
# Figure 1 — Quality metric heatmap
# ---------------------------------------------------------------------------

def _plot_quality_heatmap(df: pd.DataFrame, out_dir: Path) -> None:
    """Heatmap of all quality metrics × model/preset combinations."""
    metrics = [m for m in _QUALITY_METRICS if m in df.columns]
    if not metrics:
        return

    # Aggregate: mean per (model_id, parameter_preset)
    agg = (df.groupby(["model_id", "parameter_preset"])[metrics]
             .mean()
             .reset_index())
    agg["label"] = agg["model_id"].map(_MODEL_SHORT).fillna(agg["model_id"]) \
                   + "\n(" + agg["parameter_preset"] + ")"

    # Sort by tier then model
    tier_order = {"A-Baseline": 0, "B-Small": 1, "C-LargeCloud": 2, "optional": 3}
    agg["tier"] = agg["model_id"].map(_TIER_MAP).fillna("optional")
    agg["tier_order"] = agg["tier"].map(tier_order)
    agg = agg.sort_values(["tier_order", "model_id", "parameter_preset"])

    heat_data = agg.set_index("label")[metrics].T
    heat_data.index = [_METRIC_LABELS.get(m, m) for m in heat_data.index]

    fig, ax = plt.subplots(figsize=(max(8, len(agg) * 1.1), 4.5))
    sns.heatmap(
        heat_data,
        ax=ax,
        annot=True,
        fmt=".2f",
        cmap="YlOrRd",
        vmin=0.0,
        vmax=1.0,
        linewidths=0.4,
        linecolor="#dddddd",
        cbar_kws={"label": "Score (0–1)", "shrink": 0.8},
    )
    ax.set_title("Quality Metrics Heatmap — All Models & Presets", pad=12)
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.tick_params(axis="x", rotation=0)
    ax.tick_params(axis="y", rotation=0)

    # Tier colour bands on x-axis labels
    for i, (_, row) in enumerate(agg.iterrows()):
        ax.get_xticklabels()[i].set_color(_tier_color(row["tier"]))

    plt.tight_layout()
    path = out_dir / "quality_heatmap.png"
    fig.savefig(path)
    plt.close(fig)
    logger.info("Saved: %s", path)


# ---------------------------------------------------------------------------
# Figure 2 — Latency comparison bar chart
# ---------------------------------------------------------------------------

def _plot_latency_comparison(df: pd.DataFrame, out_dir: Path) -> None:
    """Grouped bar chart of mean latency per model, coloured by tier."""
    if "latency_seconds" not in df.columns:
        return

    agg = (df.groupby(["model_id", "parameter_preset", "tier"])["latency_seconds"]
             .mean()
             .reset_index())
    agg["model_short"] = agg["model_id"].map(_MODEL_SHORT).fillna(agg["model_id"])

    # Sort by tier then latency
    tier_order = {"A-Baseline": 0, "B-Small": 1, "C-LargeCloud": 2, "optional": 3}
    agg["tier_order"] = agg["tier"].map(tier_order)
    agg = agg.sort_values(["tier_order", "latency_seconds"])

    fig, ax = plt.subplots(figsize=(10, 5))

    presets = agg["parameter_preset"].unique()
    x = np.arange(len(agg["model_id"].unique()))
    width = 0.35
    models_ordered = agg.drop_duplicates("model_id").sort_values("tier_order")["model_id"].tolist()

    for i, preset in enumerate(["high_quality", "fast"]):
        subset = agg[agg["parameter_preset"] == preset].set_index("model_id")
        heights = [subset.loc[m, "latency_seconds"] if m in subset.index else 0 for m in models_ordered]
        colors = [_tier_color(agg[agg["model_id"] == m]["tier"].iloc[0]) for m in models_ordered]
        offset = (i - 0.5) * width
        bars = ax.bar(x + offset, heights, width, color=colors,
                      alpha=0.85 if preset == "high_quality" else 0.55,
                      edgecolor="white", linewidth=0.5,
                      label=f"{'High Quality' if preset == 'high_quality' else 'Fast'} preset")
        for bar, h in zip(bars, heights):
            if h > 0:
                ax.text(bar.get_x() + bar.get_width() / 2, h + 0.3,
                        f"{h:.1f}s", ha="center", va="bottom", fontsize=7.5)

    ax.set_xticks(x)
    ax.set_xticklabels([_MODEL_SHORT.get(m, m) for m in models_ordered], rotation=15, ha="right")
    ax.set_ylabel("Mean Latency (seconds)")
    ax.set_title("Inference Latency by Model and Preset", pad=12)
    ax.set_ylim(0, agg["latency_seconds"].max() * 1.25)

    # Tier legend patches
    tier_patches = [mpatches.Patch(color=c, label=_TIER_LABELS[t])
                    for t, c in _TIER_PALETTE.items() if t != "optional"]
    preset_handles, preset_labels = ax.get_legend_handles_labels()
    ax.legend(handles=tier_patches + preset_handles,
              labels=[_TIER_LABELS[t] for t in _TIER_PALETTE if t != "optional"] + preset_labels,
              loc="upper left", ncol=2, fontsize=8)

    plt.tight_layout()
    path = out_dir / "latency_comparison.png"
    fig.savefig(path)
    plt.close(fig)
    logger.info("Saved: %s", path)


# ---------------------------------------------------------------------------
# Figure 3 — Radar / spider chart per tier
# ---------------------------------------------------------------------------

def _plot_radar_chart(df: pd.DataFrame, out_dir: Path) -> None:
    """Radar chart comparing mean quality metrics across the 3 tiers."""
    metrics = [m for m in _QUALITY_METRICS if m in df.columns and df[m].notna().any()]
    if len(metrics) < 3:
        return

    tiers = ["A-Baseline", "B-Small", "C-LargeCloud"]
    tier_means = {}
    for tier in tiers:
        sub = df[df["tier"] == tier]
        if sub.empty:
            continue
        means = [sub[m].mean() for m in metrics]
        if any(pd.notna(v) for v in means):
            tier_means[tier] = [v if pd.notna(v) else 0.0 for v in means]

    if len(tier_means) < 2:
        logger.info("Radar chart skipped — need at least 2 tiers with data (found %d)", len(tier_means))
        return

    labels = [_METRIC_LABELS.get(m, m) for m in metrics]
    N = len(labels)
    angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
    angles += angles[:1]  # close the polygon

    fig, ax = plt.subplots(figsize=(6, 6), subplot_kw={"polar": True})

    for tier, values in tier_means.items():
        vals = values + values[:1]
        ax.plot(angles, vals, "o-", linewidth=2,
                color=_tier_color(tier), label=_TIER_LABELS[tier])
        ax.fill(angles, vals, alpha=0.12, color=_tier_color(tier))

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, size=9)
    ax.set_ylim(0, 1.05)
    ax.set_yticks([0.2, 0.4, 0.6, 0.8, 1.0])
    ax.set_yticklabels(["0.2", "0.4", "0.6", "0.8", "1.0"], size=7, color="grey")
    ax.set_title("Quality Profile by Tier", pad=20, fontweight="bold", size=12)
    ax.legend(loc="upper right", bbox_to_anchor=(1.35, 1.15), fontsize=9)
    ax.grid(color="grey", linestyle="--", linewidth=0.5, alpha=0.6)

    plt.tight_layout()
    path = out_dir / "quality_radar.png"
    fig.savefig(path)
    plt.close(fig)
    logger.info("Saved: %s", path)


# ---------------------------------------------------------------------------
# Figure 4 — Grouped bar chart per quality metric
# ---------------------------------------------------------------------------

def _plot_metric_bars(df: pd.DataFrame, out_dir: Path) -> None:
    """One subplot per quality metric showing mean score per model."""
    metrics = [m for m in _QUALITY_METRICS if m in df.columns and df[m].notna().any()]
    if not metrics:
        return

    agg = (df.groupby(["model_id", "tier"])[metrics]
             .mean()
             .reset_index())
    tier_order = {"A-Baseline": 0, "B-Small": 1, "C-LargeCloud": 2, "optional": 3}
    agg["tier_order"] = agg["tier"].map(tier_order)
    agg = agg.sort_values(["tier_order", "model_id"])
    agg["model_short"] = agg["model_id"].map(_MODEL_SHORT).fillna(agg["model_id"])

    ncols = 3
    nrows = (len(metrics) + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(14, nrows * 3.5), sharey=False)
    axes = axes.flatten()

    colors = [_tier_color(t) for t in agg["tier"]]

    for i, metric in enumerate(metrics):
        ax = axes[i]
        bars = ax.bar(
            range(len(agg)),
            agg[metric],
            color=colors,
            edgecolor="white",
            linewidth=0.5,
            width=0.65,
        )
        ax.set_xticks(range(len(agg)))
        ax.set_xticklabels(agg["model_short"], rotation=30, ha="right", fontsize=8)
        ax.set_ylim(0, 1.12)
        ax.set_title(_METRIC_LABELS.get(metric, metric), fontsize=10, fontweight="bold")
        ax.set_ylabel("Mean Score")
        ax.axhline(1.0, color="grey", linestyle="--", linewidth=0.7, alpha=0.5)
        for bar, val in zip(bars, agg[metric]):
            if pd.notna(val) and val > 0:
                ax.text(bar.get_x() + bar.get_width() / 2, val + 0.02,
                        f"{val:.2f}", ha="center", va="bottom", fontsize=7.5)

    # Hide unused subplots
    for j in range(len(metrics), len(axes)):
        axes[j].set_visible(False)

    # Shared tier legend
    tier_patches = [mpatches.Patch(color=c, label=_TIER_LABELS[t])
                    for t, c in _TIER_PALETTE.items() if t != "optional"]
    fig.legend(handles=tier_patches, loc="lower center", ncol=3,
               bbox_to_anchor=(0.5, -0.02), fontsize=9, framealpha=0.9)

    fig.suptitle("Quality Metrics by Model (mean across all runs)", fontsize=13,
                 fontweight="bold", y=1.01)
    plt.tight_layout()
    path = out_dir / "metric_bars.png"
    fig.savefig(path)
    plt.close(fig)
    logger.info("Saved: %s", path)


# ---------------------------------------------------------------------------
# Figure 5 — Fact fidelity distribution (violin + strip)
# ---------------------------------------------------------------------------

def _plot_fact_fidelity_dist(df: pd.DataFrame, out_dir: Path) -> None:
    """Violin + strip plot showing distribution of fact_fidelity_score per model."""
    if "fact_fidelity_score" not in df.columns:
        return

    plot_df = df[df["fact_fidelity_score"].notna()].copy()
    if plot_df.empty:
        return

    tier_order = {"A-Baseline": 0, "B-Small": 1, "C-LargeCloud": 2, "optional": 3}
    plot_df["tier_order"] = plot_df["tier"].map(tier_order)
    plot_df = plot_df.sort_values(["tier_order", "model_id"])
    model_order = plot_df["model_id"].unique().tolist()
    model_labels = [_MODEL_SHORT.get(m, m) for m in model_order]

    fig, ax = plt.subplots(figsize=(10, 5))

    palette = {m: _tier_color(_TIER_MAP.get(m, "optional")) for m in model_order}

    sns.violinplot(
        data=plot_df,
        x="model_id",
        y="fact_fidelity_score",
        order=model_order,
        hue="model_id",
        hue_order=model_order,
        palette=palette,
        inner=None,
        cut=0,
        linewidth=1.2,
        ax=ax,
        alpha=0.6,
        legend=False,
    )
    sns.stripplot(
        data=plot_df,
        x="model_id",
        y="fact_fidelity_score",
        order=model_order,
        hue="model_id",
        hue_order=model_order,
        palette=palette,
        size=5,
        jitter=True,
        alpha=0.85,
        ax=ax,
        legend=False,
    )

    ax.set_xticks(range(len(model_order)))
    ax.set_xticklabels(model_labels, rotation=15, ha="right")
    ax.set_xlabel("")
    ax.set_ylabel("Fact Fidelity Score")
    ax.set_ylim(-0.05, 1.15)
    ax.set_title("Fact Fidelity Score Distribution by Model", pad=12)
    ax.axhline(1.0, color="grey", linestyle="--", linewidth=0.7, alpha=0.5)

    tier_patches = [mpatches.Patch(color=c, label=_TIER_LABELS[t])
                    for t, c in _TIER_PALETTE.items() if t != "optional"]
    ax.legend(handles=tier_patches, loc="lower right", fontsize=9)

    plt.tight_layout()
    path = out_dir / "fact_fidelity_dist.png"
    fig.savefig(path)
    plt.close(fig)
    logger.info("Saved: %s", path)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_visualizations(results_csv: Path, output_dir: Path) -> None:
    """Generate all 5 research-paper-quality figures from results.csv.

    Args:
        results_csv: Path to the results.csv produced by the comparison pipeline.
        output_dir:  Root output directory (visuals saved to output_dir/visuals/).
    """
    visuals_dir = output_dir / "visuals"
    visuals_dir.mkdir(parents=True, exist_ok=True)

    try:
        df = _load_results(results_csv)
    except Exception as exc:
        logger.error("Could not load results CSV for visualization: %s", exc)
        return

    if df.empty:
        logger.warning("No data in results CSV — skipping visualizations.")
        return

    _apply_paper_style()

    logger.info("Generating visualizations from %d runs -> %s", len(df), visuals_dir)

    _plot_quality_heatmap(df, visuals_dir)
    _plot_latency_comparison(df, visuals_dir)
    _plot_radar_chart(df, visuals_dir)
    _plot_metric_bars(df, visuals_dir)
    _plot_fact_fidelity_dist(df, visuals_dir)

    logger.info(
        "Visualizations complete — 5 figures saved to %s/", visuals_dir
    )
