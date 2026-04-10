#!/usr/bin/env python3
"""
scripts/run_model_comparison.py

CLI for running the 3-tier SLM model comparison experiment.

Tier A — Baseline (Groq cloud):
  llama-3.1-8b, llama-3.3-70b

Tier B — Small model viability (Ollama local, ~4B params):
  gemma-4-e4b, qwen-3.5-4b

Tier C — Large model viability (Ollama cloud, no local RAM):
  gemma-4-31b-cloud, qwen-3.5-397b-cloud

Usage examples:

  # Run all 6 required pipeline models across all 7 document types
  python scripts/run_model_comparison.py --models all --doc-types all

  # Run a single doc type for quick validation
  python scripts/run_model_comparison.py --models all --doc-types sale_deed

  # Run specific models and a single preset
  python scripts/run_model_comparison.py --models llama-3.1-8b gemma-4-e4b --presets high_quality

  # Include optional models (qwen-2.5-7b, gemma-4-9b, qwen-3-32b, gemma-4-31b)
  python scripts/run_model_comparison.py --models all --include-large-models

  # Custom output directory
  python scripts/run_model_comparison.py --models all --doc-types sale_deed --output-dir ./my_results

All outputs are saved to output/model_comparison/ by default:
  results.csv       — one row per run
  summary.csv       — mean/std per model-preset combination
  results.json      — nested structure for programmatic access
  summary.md        — Markdown report with tier-grouped tables
  intermediate/     — per-run JSON written after each combination completes
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import warnings
from pathlib import Path

# Suppress Pydantic v1/Python 3.14 compatibility warning from langchain
warnings.filterwarnings("ignore", message="Core Pydantic V1 functionality")

# Load .env before anything else so GROQ_API_KEY and other vars are available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Ensure project root is on sys.path when run directly
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config.settings import settings


def _configure_logging(json_logs: bool) -> None:
    """Configure logging — plain text by default, JSON when --json-logs is set."""
    if json_logs:
        try:
            from pythonjsonlogger import jsonlogger
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(jsonlogger.JsonFormatter(
                fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            ))
            logging.root.setLevel(logging.INFO)
            logging.root.addHandler(handler)
            return
        except ImportError:
            pass  # fall through to plain text if pythonjsonlogger not installed

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


logger = logging.getLogger("run_model_comparison")

# ---------------------------------------------------------------------------
# Document type → minimal fact pattern mapping
# ---------------------------------------------------------------------------

_FACT_PATTERNS: dict[str, dict] = {
    "sale_deed": {
        "doc_type": "sale_deed",
        "vendor_name": "Shri Rajesh Kumar Sharma",
        "vendor_address": "123, MG Road, Pune, Maharashtra 411001",
        "purchaser_name": "Smt. Priya Anil Desai",
        "purchaser_address": "456, FC Road, Pune, Maharashtra 411004",
        "property_description": "Plot No. 7, Survey No. 123/4A, Village Kothrud, Taluka Haveli, District Pune",
        "consideration_amount": "5000000",
        "payment_mode": "Bank Transfer",
        "registration_date": "15/04/2026",
    },
    "mortgage_deed": {
        "doc_type": "mortgage_deed",
        "mortgagor_name": "Shri Vikram Anand Kulkarni",
        "mortgagor_address": "789, Shivaji Nagar, Pune, Maharashtra 411005",
        "mortgagee_name": "State Bank of India, Pune Main Branch",
        "mortgagee_address": "1, MG Road, Pune, Maharashtra 411001",
        "property_description": "Flat No. 302, Shree Apartments, Survey No. 45/2, Baner, Pune",
        "loan_amount": "2500000",
        "interest_rate": "8.5",
        "loan_tenure_years": "20",
    },
    "power_of_attorney": {
        "doc_type": "power_of_attorney",
        "principal_name": "Shri Suresh Baburao Patil",
        "principal_address": "22, Laxmi Road, Nashik, Maharashtra 422001",
        "attorney_name": "Shri Mahesh Suresh Patil",
        "attorney_address": "45, College Road, Nashik, Maharashtra 422005",
        "poa_type": "Special Power of Attorney",
        "powers_granted": "Sell property, Execute documents",
        "property_description": "Agricultural land Survey No. 88/1, Village Ozar, Taluka Niphad, Nashik",
    },
    "leave_and_license": {
        "doc_type": "leave_and_license",
        "licensor_name": "Smt. Kavita Ramesh Joshi",
        "licensor_address": "12, Tilak Road, Mumbai, Maharashtra 400028",
        "licensee_name": "Shri Amit Prakash Verma",
        "licensee_address": "67, Linking Road, Bandra, Mumbai 400050",
        "property_description": "Flat No. 501, Sea View Apartments, Bandra West, Mumbai",
        "license_period_months": "11",
        "monthly_license_fee": "35000",
        "security_deposit": "105000",
    },
    "gift_deed": {
        "doc_type": "gift_deed",
        "donor_name": "Shri Ganesh Narayan Bhosale",
        "donor_address": "34, Peth Road, Satara, Maharashtra 415001",
        "donee_name": "Ku. Sneha Ganesh Bhosale",
        "donee_relationship": "Daughter",
        "property_description": "House No. 7, Gat No. 234, Village Koregaon, Taluka Koregaon, Satara",
        "gift_date": "10/04/2026",
    },
    "conveyance_deed": {
        "doc_type": "conveyance_deed",
        "conveyor_name": "M/s. Sunrise Developers Pvt. Ltd.",
        "conveyor_address": "Office No. 5, Business Park, Viman Nagar, Pune 411014",
        "transferee_name": "Shri Deepak Mohan Sawant",
        "transferee_address": "89, Karve Road, Pune, Maharashtra 411004",
        "property_description": "Flat No. 204, Tower B, Sunrise Heights, Survey No. 67/3, Wakad, Pune",
        "consideration_amount": "7500000",
        "possession_date": "01/05/2026",
    },
    "affidavit": {
        "doc_type": "affidavit",
        "deponent_name": "Shri Ravi Shankar Mishra",
        "deponent_address": "56, Civil Lines, Nagpur, Maharashtra 440001",
        "deponent_age": "42",
        "deponent_occupation": "Government Employee",
        "affidavit_purpose": "Change of name in official records",
        "statement": "I hereby solemnly affirm that my name has been changed from Ravi Mishra to Ravi Shankar Mishra in all official documents.",
        "place": "Nagpur",
        "date": "10/04/2026",
    },
}

_ALL_DOC_TYPES = list(_FACT_PATTERNS.keys())

# Tier A — Groq baseline
_TIER_A = ["llama-3.1-8b", "llama-3.3-70b"]
# Tier B — small model viability (Ollama local)
_TIER_B = ["gemma-4-e4b", "qwen-3.5-4b"]
# Tier C — large model viability (Ollama cloud)
_TIER_C = ["gemma-4-31b-cloud", "qwen-3.5-397b-cloud"]

# All 6 required pipeline models (default for --models all)
_ALL_REQUIRED_MODELS = _TIER_A + _TIER_B + _TIER_C

# Tier label lookup for summary table grouping
_TIER_LABEL: dict[str, str] = {
    **{m: "A-Baseline" for m in _TIER_A},
    **{m: "B-Small" for m in _TIER_B},
    **{m: "C-LargeCloud" for m in _TIER_C},
}

_ALL_PRESETS = ["high_quality", "fast"]


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run 3-tier SLM model comparison for Maharashtra Legal Document Generation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=["all"],
        metavar="MODEL",
        help=(
            "Models to compare. Use 'all' for all 6 required pipeline models "
            "(Tier A: llama-3.1-8b, llama-3.3-70b | "
            "Tier B: gemma-4-e4b, qwen-3.5-4b | "
            "Tier C: gemma-4-31b-cloud, qwen-3.5-397b-cloud). "
            "Or specify registry keys directly. Default: all"
        ),
    )
    parser.add_argument(
        "--presets",
        nargs="+",
        default=["high_quality", "fast"],
        choices=["high_quality", "fast"],
        metavar="PRESET",
        help="Parameter presets to use. Default: high_quality fast",
    )
    parser.add_argument(
        "--doc-types",
        nargs="+",
        default=["all"],
        metavar="DOC_TYPE",
        help=(
            "Document types to generate. Use 'all' for all 7 types. "
            "Or specify: sale_deed, mortgage_deed, power_of_attorney, "
            "leave_and_license, gift_deed, conveyance_deed, affidavit. Default: all"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        metavar="DIR",
        help=(
            "Directory for output files. "
            "Defaults to MODEL_COMPARISON_OUTPUT_DIR env var or ./output/model_comparison"
        ),
    )
    parser.add_argument(
        "--include-large-models",
        action="store_true",
        default=False,
        help=(
            "Also include optional models (qwen-2.5-7b, gemma-4-9b, qwen-3-32b, gemma-4-31b). "
            "Local Ollama models require sufficient RAM."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help=(
            "Resume an interrupted run. Reads completed combinations from "
            "output_dir/intermediate/ and skips them. Safe to re-run after any failure."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help=(
            "Validate connectivity only — check all Groq API keys and Ollama "
            "reachability without generating any documents. Exits 0 if all checks pass."
        ),
    )
    parser.add_argument(
        "--json-logs",
        action="store_true",
        default=False,
        help=(
            "Emit logs as JSON (one object per line) for CloudWatch / Stackdriver ingestion. "
            "Requires: pip install python-json-logger"
        ),
    )
    return parser


def _resolve_models(model_args: list[str], include_large: bool) -> list[str]:
    """Resolve --models argument to a list of registry keys."""
    from src.model_comparison.model_registry import MODEL_REGISTRY, get_available_models

    if model_args == ["all"]:
        if include_large:
            # All models that fit in available RAM (cloud models always included)
            available = get_available_models(include_large=True)
            return [key for key, cfg in MODEL_REGISTRY.items() if cfg in available]
        else:
            # Only the 6 required pipeline models
            return _ALL_REQUIRED_MODELS

    # Validate each specified model key
    valid_keys = set(MODEL_REGISTRY.keys())
    resolved = []
    for m in model_args:
        if m in valid_keys:
            resolved.append(m)
        else:
            logger.error("Unknown model key '%s'. Valid keys: %s", m, ", ".join(sorted(valid_keys)))
            sys.exit(1)
    return resolved


def _resolve_doc_types(doc_type_args: list[str]) -> list[str]:
    """Resolve --doc-types argument to a list of doc type strings."""
    if doc_type_args == ["all"]:
        return _ALL_DOC_TYPES

    valid = set(_ALL_DOC_TYPES)
    resolved = []
    for dt in doc_type_args:
        if dt in valid:
            resolved.append(dt)
        else:
            logger.error(
                "Unknown doc type '%s'. Valid types: %s", dt, ", ".join(sorted(valid))
            )
            sys.exit(1)
    return resolved


# ---------------------------------------------------------------------------
# Resume support
# ---------------------------------------------------------------------------

def _load_completed_combinations(output_dir: Path) -> set[tuple[str, str, str]]:
    """Scan intermediate/ directory and return set of (model, preset, doc_type) already done."""
    intermediate_dir = output_dir / "intermediate"
    if not intermediate_dir.exists():
        return set()

    completed: set[tuple[str, str, str]] = set()
    for path in intermediate_dir.glob("*.json"):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            model = data.get("model_id", "")
            preset = data.get("parameter_preset", "")
            doc_type = data.get("doc_type", "")
            if model and preset and doc_type:
                completed.add((model, preset, doc_type))
        except Exception:
            pass  # corrupt file — skip

    if completed:
        logger.info("Resume: found %d completed combinations in %s", len(completed), intermediate_dir)
    return completed


def _load_runs_from_intermediate(output_dir: Path) -> list:
    """Load ComparisonRun objects from intermediate JSON files for resume."""
    from src.model_comparison.comparator import ComparisonRun
    intermediate_dir = output_dir / "intermediate"
    runs = []
    if not intermediate_dir.exists():
        return runs
    for path in sorted(intermediate_dir.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            run = ComparisonRun(
                run_id=data["run_id"],
                timestamp=data["timestamp"],
                model_id=data["model_id"],
                parameter_preset=data["parameter_preset"],
                doc_type=data["doc_type"],
                fact_pattern=data.get("fact_pattern", {}),
                generated_text=data.get("generated_text", ""),
                evaluation_result=data.get("evaluation_result", {}),
                latency_seconds=data.get("latency_seconds", 0.0),
                token_count=data.get("token_count", 0),
                backend=data.get("backend", ""),
            )
            runs.append(run)
        except Exception as exc:
            logger.warning("Could not load intermediate file %s: %s", path.name, exc)
    return runs


# ---------------------------------------------------------------------------
# Dry-run connectivity check
# ---------------------------------------------------------------------------

def _dry_run_check(models: list[str]) -> bool:
    """Validate API keys and Ollama connectivity. Returns True if all checks pass."""
    import requests as _requests
    from src.model_comparison.model_registry import MODEL_REGISTRY
    from src.model_comparison.backends import _load_groq_api_keys

    all_ok = True
    groq_models = [m for m in models if MODEL_REGISTRY[m].provider == "groq"]
    ollama_models = [m for m in models if MODEL_REGISTRY[m].provider == "ollama"]

    # --- Groq key validation ---
    if groq_models:
        api_keys = _load_groq_api_keys()
        if not api_keys:
            logger.error("DRY-RUN FAIL: No Groq API keys found. Set GROQ_API_KEY in .env")
            all_ok = False
        else:
            for i, key in enumerate(api_keys, 1):
                try:
                    resp = _requests.get(
                        "https://api.groq.com/openai/v1/models",
                        headers={"Authorization": f"Bearer {key}"},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        logger.info("DRY-RUN OK: Groq key #%d is valid", i)
                    elif resp.status_code == 401:
                        logger.error("DRY-RUN FAIL: Groq key #%d is invalid (401)", i)
                        all_ok = False
                    else:
                        logger.warning("DRY-RUN WARN: Groq key #%d returned HTTP %d", i, resp.status_code)
                except Exception as exc:
                    logger.error("DRY-RUN FAIL: Groq key #%d unreachable: %s", i, exc)
                    all_ok = False

    # --- Ollama connectivity ---
    if ollama_models:
        try:
            resp = _requests.get("http://localhost:11434/api/tags", timeout=5)
            if resp.status_code == 200:
                pulled = {m["name"] for m in resp.json().get("models", [])}
                logger.info("DRY-RUN OK: Ollama reachable — %d models pulled", len(pulled))
                for model_key in ollama_models:
                    model_id = MODEL_REGISTRY[model_key].model_id
                    ram = MODEL_REGISTRY[model_key].ram_required_gb
                    if ram == 0:
                        logger.info("DRY-RUN OK: %s is cloud-hosted (no pull needed)", model_id)
                    elif model_id in pulled:
                        logger.info("DRY-RUN OK: %s is pulled and ready", model_id)
                    else:
                        logger.warning("DRY-RUN WARN: %s not found in ollama list — run: ollama pull %s", model_id, model_id)
            else:
                logger.error("DRY-RUN FAIL: Ollama returned HTTP %d", resp.status_code)
                all_ok = False
        except Exception as exc:
            logger.error("DRY-RUN FAIL: Ollama not reachable at localhost:11434 — %s", exc)
            all_ok = False

    status = "PASS" if all_ok else "FAIL"
    logger.info("DRY-RUN %s: %d/%d checks passed", status, len(models), len(models))
    return all_ok


# ---------------------------------------------------------------------------
# Summary table printer
# ---------------------------------------------------------------------------

def _print_summary_table(report) -> None:
    """Print a tier-grouped summary table with all evaluation metrics."""
    from src.model_comparison.reporter import compute_aggregate_stats, identify_best_models

    summary = report.summary or compute_aggregate_stats(report.runs)
    best = report.best_models or identify_best_models(summary)

    all_metrics = [
        "cache_hit_rate", "slot_fill_rate", "fact_fidelity_score",
        "section_completeness", "citation_format_compliance",
        "jurisdictional_accuracy", "latency_seconds",
    ]
    # Short column headers
    col_headers = {
        "cache_hit_rate":             "CHR",
        "slot_fill_rate":             "SFR",
        "fact_fidelity_score":        "FFid",
        "section_completeness":       "SecC",
        "citation_format_compliance": "CitF",
        "jurisdictional_accuracy":    "JurA",
        "latency_seconds":            "Lat(s)",
    }

    W = 110
    print("\n" + "=" * W)
    print("MODEL COMPARISON RESULTS — 3-TIER PIPELINE")
    print("=" * W)
    hdr = f"{'Model / Preset':<38} {'Tier':<14}"
    for m in all_metrics:
        w = 8 if m == "latency_seconds" else 6
        hdr += f" {col_headers[m]:>{w}}"
    print(hdr)
    print("-" * W)

    tier_order = ["A-Baseline", "B-Small", "C-LargeCloud"]
    combos_by_tier: dict[str, list[str]] = {t: [] for t in tier_order}
    ungrouped: list[str] = []

    for combo in sorted(summary.keys()):
        model_key = combo.split("/")[0]
        tier = _TIER_LABEL.get(model_key, "")
        (combos_by_tier[tier] if tier in combos_by_tier else ungrouped).append(combo)

    tier_names = {
        "A-Baseline":   "-- Tier A: Baseline (Groq) --",
        "B-Small":      "-- Tier B: Small Models (Local) --",
        "C-LargeCloud": "-- Tier C: Large Cloud (Ollama) --",
    }

    def _row(combo: str, tier_label: str) -> None:
        metrics = summary[combo]
        row = f"  {combo:<36} {tier_label:<14}"
        for m in all_metrics:
            val = metrics.get(m, {}).get("mean", float("nan"))
            w = 8 if m == "latency_seconds" else 6
            row += f" {val:>{w}.3f}"
        print(row)

    for tier in tier_order:
        if not combos_by_tier[tier]:
            continue
        print(f"\n  {tier_names[tier]}")
        for combo in combos_by_tier[tier]:
            _row(combo, tier)
    for combo in ungrouped:
        _row(combo, "optional")

    print("\n" + "-" * W)
    print("Best per metric:")
    for m in all_metrics:
        if m in best:
            model_key = best[m].split("/")[0]
            tier = _TIER_LABEL.get(model_key, "optional")
            print(f"  {col_headers[m]:<6} ({m:<32}) -> {best[m]}  [{tier}]")

    print(f"\nSuccessful: {report.successful_runs}/{report.total_runs}  "
          f"Failed: {len(report.failed_runs)}  "
          f"Duration: {report.total_duration_seconds:.1f}s")

    if report.failed_runs:
        print("\nFailed runs:")
        for f in report.failed_runs:
            print(f"  {f.get('model','?')}/{f.get('preset','?')}/{f.get('doc_type','?')}: "
                  f"{f.get('error','?')}")
    print("=" * W + "\n")


# ---------------------------------------------------------------------------
# Document saving — PDF + JSON
# ---------------------------------------------------------------------------

def _save_generated_documents(report, output_dir: Path) -> None:
    """
    Save each generated document as both a PDF and a JSON file under
    output_dir/documents/<model>__<preset>__<doc_type>__<run_id[:8]>/
    """
    if not report.runs:
        return

    docs_dir = output_dir / "documents"
    docs_dir.mkdir(exist_ok=True)

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.units import cm
        _reportlab_ok = True
    except ImportError:
        logger.warning("reportlab not available — skipping PDF export for documents")
        _reportlab_ok = False

    saved_pdf = 0
    saved_json = 0

    for run in report.runs:
        safe_model = run.model_id.replace("/", "_").replace(":", "_")
        stem = f"{safe_model}__{run.parameter_preset}__{run.doc_type}__{run.run_id[:8]}"
        run_dir = docs_dir / stem
        run_dir.mkdir(exist_ok=True)

        # --- JSON ---
        json_path = run_dir / "document.json"
        doc_data = {
            "run_id": run.run_id,
            "model_id": run.model_id,
            "parameter_preset": run.parameter_preset,
            "doc_type": run.doc_type,
            "backend": run.backend,
            "latency_seconds": run.latency_seconds,
            "token_count": run.token_count,
            "fact_pattern": run.fact_pattern,
            "generated_text": run.generated_text,
            "evaluation_result": run.evaluation_result,
        }
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(doc_data, fh, indent=2, ensure_ascii=False)
        saved_json += 1

        # --- PDF ---
        if _reportlab_ok and run.generated_text:
            pdf_path = run_dir / "document.pdf"
            try:
                styles = getSampleStyleSheet()
                doc = SimpleDocTemplate(
                    str(pdf_path),
                    pagesize=A4,
                    leftMargin=2*cm, rightMargin=2*cm,
                    topMargin=2*cm, bottomMargin=2*cm,
                )
                story = []
                # Title
                title = f"{run.doc_type.replace('_', ' ').title()} — {run.model_id} ({run.parameter_preset})"
                story.append(Paragraph(title, styles["Title"]))
                story.append(Spacer(1, 0.5*cm))
                # Metadata block
                meta_lines = [
                    f"Run ID: {run.run_id}",
                    f"Backend: {run.backend}  |  Latency: {run.latency_seconds:.2f}s  |  Tokens: {run.token_count}",
                ]
                for line in meta_lines:
                    story.append(Paragraph(line, styles["Normal"]))
                story.append(Spacer(1, 0.5*cm))
                # Document body — split on newlines, render each paragraph
                for para in run.generated_text.split("\n"):
                    para = para.strip()
                    if not para:
                        story.append(Spacer(1, 0.2*cm))
                        continue
                    # Escape XML special chars for reportlab
                    para = (para
                            .replace("&", "&amp;")
                            .replace("<", "&lt;")
                            .replace(">", "&gt;")
                            .replace("\x00", "")  # strip null bytes
                            )
                    # Strip any remaining non-printable ASCII control chars
                    para = "".join(c for c in para if ord(c) >= 32 or c in "\t")
                    if para:
                        story.append(Paragraph(para, styles["Normal"]))
                doc.build(story)
                saved_pdf += 1
            except Exception as exc:
                logger.warning("PDF export failed for run %s: %s", run.run_id[:8], exc)

    logger.info(
        "Documents saved to %s/ — %d JSON, %d PDF",
        docs_dir, saved_json, saved_pdf,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # Configure logging first (before any log calls)
    _configure_logging(args.json_logs)

    # Feature flag gate
    if not settings.model_comparison_enabled:
        logger.error(
            "Model comparison is disabled. "
            "Set ENABLE_MODEL_COMPARISON=true (and ENABLE_PHASE_1_5=true) to enable."
        )
        sys.exit(1)

    # Resolve output directory
    output_dir = Path(args.output_dir or settings.model_comparison_output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "intermediate").mkdir(exist_ok=True)

    # Resolve models and doc types
    include_large = args.include_large_models or settings.include_large_models
    models = _resolve_models(args.models, include_large)
    doc_types = _resolve_doc_types(args.doc_types)
    presets = args.presets

    if not models:
        logger.error("No models available. Check RAM requirements or specify models explicitly.")
        sys.exit(1)

    # --- Dry-run mode ---
    if args.dry_run:
        logger.info("Running connectivity checks (--dry-run)...")
        ok = _dry_run_check(models)
        sys.exit(0 if ok else 1)

    # Log tier breakdown
    tier_counts = {"A-Baseline": 0, "B-Small": 0, "C-LargeCloud": 0, "optional": 0}
    for m in models:
        tier_counts[_TIER_LABEL.get(m, "optional")] += 1
    logger.info("Pipeline tiers: Tier A=%d  Tier B=%d  Tier C=%d  optional=%d",
                tier_counts["A-Baseline"], tier_counts["B-Small"],
                tier_counts["C-LargeCloud"], tier_counts["optional"])
    logger.info("Models:    %s", models)
    logger.info("Presets:   %s", presets)
    logger.info("Doc types: %s", doc_types)
    logger.info("Output:    %s", output_dir)

    # --- Resume mode: load already-completed combinations ---
    completed_combinations: set[tuple[str, str, str]] = set()
    prior_runs: list = []
    if args.resume:
        completed_combinations = _load_completed_combinations(output_dir)
        prior_runs = _load_runs_from_intermediate(output_dir)
        if completed_combinations:
            logger.info(
                "Resume: skipping %d already-completed combinations",
                len(completed_combinations),
            )

    total = len(models) * len(presets) * len(doc_types)
    remaining = total - len(completed_combinations)
    logger.info("Total runs planned: %d  (remaining: %d)", total, remaining)

    # Build fact patterns for selected doc types
    fact_patterns = {dt: _FACT_PATTERNS[dt] for dt in doc_types}

    # Run comparison (passes completed set for skipping)
    from src.model_comparison.comparator import run_comparison
    report = run_comparison(
        models=models,
        presets=presets,
        doc_types=doc_types,
        fact_patterns=fact_patterns,
        output_dir=output_dir,
        skip_combinations=completed_combinations,
        prior_runs=prior_runs,
    )

    # Export all results to output/model_comparison/
    from src.model_comparison.reporter import (
        export_csv,
        export_summary_csv,
        export_json,
        generate_summary_markdown,
    )

    export_csv(report, output_dir / "results.csv", deduplicate=True)
    export_summary_csv(report, output_dir / "summary.csv")
    export_json(report, output_dir / "results.json")
    generate_summary_markdown(report, output_dir / "summary.md")

    logger.info("Results written to %s/", output_dir)
    logger.info("  results.csv   — %d rows (one per run)", report.successful_runs)
    logger.info("  summary.csv   — per model-preset aggregates")
    logger.info("  results.json  — nested structure for programmatic access")
    logger.info("  summary.md    — Markdown report with tier comparison tables")
    logger.info("  intermediate/ — per-run JSON snapshots")

    # Save generated documents as PDF + JSON
    _save_generated_documents(report, output_dir)

    # Generate research-paper visualizations
    from src.model_comparison.visualizer import generate_visualizations
    generate_visualizations(output_dir / "results.csv", output_dir)

    # Print tier-grouped summary table to stdout
    _print_summary_table(report)

    # Exit with error code if all runs failed (useful for CI)
    if report.failed_runs and report.successful_runs == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
