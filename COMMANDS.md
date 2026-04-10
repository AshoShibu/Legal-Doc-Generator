# Maharashtra Legal Document Generation — Command Reference

Quick reference for all commands to run, test, and evaluate the system.

---

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env — add GROQ_API_KEY at minimum
```

Get your free Groq API key at: https://console.groq.com/keys

### 3. Pull Ollama Models (Phase 1.5 model comparison)

```bash
# Tier B — small local models (~4B params)
ollama pull gemma4:e4b       # ~9.6 GB disk
ollama pull qwen3.5:4b       # ~3.4 GB disk

# Tier C — cloud-hosted (no pull needed, login required)
ollama login
# ollama run gemma4:31b-cloud      # connects on first use
# ollama run qwen3.5:397b-cloud    # connects on first use

# Phase 1 local inference (optional)
ollama pull llama3:8b
```

---

## Phase 1 — Document Generation

### Launch React Frontend with API

```bash
uvicorn backend.main:app --reload
cd frontend
npm install
npm start
```

API: http://localhost:8000
UI: http://localhost:3000

### Full Pipeline (All 7 Document Types)

```bash
python scripts/run_phase1_workflow.py
```

Output: `output/cag/{doc_type}/{run_id}/`

### Individual Document Generation

```bash
python scripts/generate_docs_individually.py
```

Output: `output/individual/{doc_type}/{run_id}/`

### Generate Single Document (Llama 3.1 8B)

```bash
python scripts/generate_doc.py
```

### Generate Single Document (Llama 3.3 70B)

```bash
python scripts/generate_llama70b_doc.py
```

---

## Phase 1.5 — Model Comparison Pipeline

### Validate Connectivity Before a Long Run

```bash
python scripts/run_model_comparison.py --models all --dry-run
```

Checks all Groq API keys and Ollama reachability. Exits 0 on pass, 1 on failure.

### Run All 6 Models on a Single Document Type

```bash
python scripts/run_model_comparison.py --models all --doc-types sale_deed
```

### Run All 6 Models on All 7 Document Types

```bash
python scripts/run_model_comparison.py --models all --doc-types all
```

### Run Specific Models

```bash
# Tier A only (Groq baseline)
python scripts/run_model_comparison.py --models llama-3.1-8b llama-3.3-70b --doc-types all

# Tier B only (small local models)
python scripts/run_model_comparison.py --models gemma-4-e4b qwen-3.5-4b --doc-types sale_deed

# Single preset
python scripts/run_model_comparison.py --models all --doc-types sale_deed --presets high_quality
```

### Resume an Interrupted Run

```bash
python scripts/run_model_comparison.py --models all --doc-types all --resume
```

Reads completed combinations from `output/model_comparison/intermediate/` and skips them.

### Cloud Deployment with JSON Logs

```bash
python scripts/run_model_comparison.py --models all --doc-types all --json-logs
```

Emits one JSON object per log line — compatible with CloudWatch, Stackdriver, Datadog.

### Include Optional Large Models

```bash
python scripts/run_model_comparison.py --models all --include-large-models --doc-types sale_deed
```

Includes qwen-2.5-7b, gemma-4-9b, qwen-3-32b, gemma-4-31b (requires sufficient RAM).

### Custom Output Directory

```bash
python scripts/run_model_comparison.py --models all --doc-types all --output-dir ./my_results
```

### Outputs

All results saved to `output/model_comparison/` (or `--output-dir`):

```
output/model_comparison/
├── results.csv          # One row per run, all 7 metrics (deduplicated)
├── summary.csv          # Mean ± std per model-preset combination
├── results.json         # Nested structure for programmatic access
├── summary.md           # Markdown report with tier comparison tables
├── intermediate/        # Per-run JSON snapshots (used by --resume)
├── documents/           # Per-run PDF + JSON generated documents
│   └── {model}__{preset}__{doc_type}__{run_id[:8]}/
│       ├── document.pdf
│       └── document.json
└── visuals/             # 5 research-paper figures (auto-generated)
    ├── quality_heatmap.png
    ├── latency_comparison.png
    ├── quality_radar.png
    ├── metric_bars.png
    └── fact_fidelity_dist.png
```

---

## Phase 1.5 — Visualizations Only

Regenerate visualizations from existing results without re-running the pipeline:

```bash
python -c "
from pathlib import Path
from src.model_comparison.visualizer import generate_visualizations
generate_visualizations(Path('output/model_comparison/results.csv'), Path('output/model_comparison'))
"
```

---

## Phase 1.5 — Benchmarking

### Latency Benchmark (Citation Formatting + Underlining)

```bash
python scripts/benchmark_citations_formatting.py
```

Verifies: citation formatting ≤50ms, underlining ≤100ms per document.

### RAGAS Tier 3 Evaluation (standalone)

```bash
python scripts/test_ragas_integration.py
```

Requires `GROQ_RAGAS_API_KEY` in `.env`. Output: `output/ragas_test_result.json`

---

## Testing

### Run All Tests

```bash
pytest -v
```

### Phase 1 Tests

```bash
pytest tests/ocr/ tests/pii/ tests/cag/ tests/intake/ -v
```

### Phase 1.5 — Citation Formatter (31 tests)

```bash
pytest tests/generation/test_citation_formatter.py -v
```

### Phase 1.5 — Formatting Engine (34 tests)

```bash
pytest tests/generation/test_formatting_engine.py -v
```

### Phase 1.5 — Model Comparison (mocked, no API keys needed)

```bash
pytest tests/test_model_comparison.py -v
```

### Property-Based Tests

```bash
pytest tests/cag/test_cag_properties.py -v
pytest tests/generation/test_generation_properties.py -v
```

### With Coverage

```bash
pytest --cov=src --cov-report=html
# View: htmlcov/index.html
```

---

## Evaluation

### Evaluate a Single Generated PDF

```bash
python -W ignore scripts/evaluate_single_pdf.py "path/to/generated.pdf"
```

Output: `output/evaluation_results.json`

### Evaluate All Generated Documents (BLEU + ROUGE)

```bash
python scripts/evaluate_documents.py
```

---

## Docker

### Build and Run FastAPI + React App

```bash
docker build -t maharashtra-legal .
docker run -p 8000:8000 --env-file .env maharashtra-legal
```

### Build and Run Model Comparison

```bash
docker build -f Dockerfile.model_comparison -t maharashtra-model-comparison .

# Dry-run (validate connectivity)
docker run --rm -e GROQ_API_KEY=gsk_... maharashtra-model-comparison --models all --dry-run

# Full run with JSON logs
docker run --rm \
  -e GROQ_API_KEY=gsk_... \
  -v $(pwd)/output:/app/output \
  maharashtra-model-comparison \
  --models all --doc-types all --json-logs
```

---

## Troubleshooting

### Groq Rate Limits (HTTP 429)

Add multiple free Groq API keys to `.env` for automatic rotation:

```bash
GROQ_API_KEY=gsk_...
GROQ_API_KEY_2=gsk_...
GROQ_API_KEY_3=gsk_...
```

The backend rotates keys on 429 and waits for quota refresh when all keys are exhausted.

### Ollama Cloud Models Return 401

```bash
ollama login
```

Cloud-hosted models (gemma4:31b-cloud, qwen3.5:397b-cloud) require Ollama authentication.

### Ollama Cloud Models Timeout

Cloud models can take 2-3 minutes for longer document types. The timeout is set to 180s. If you still hit timeouts, try:

```bash
python scripts/run_model_comparison.py --models gemma-4-31b-cloud --doc-types sale_deed --presets fast
```

### spaCy Model Not Found

```bash
python -m spacy download en_core_web_sm
```

### NLTK Tokenizer Missing

```bash
python -c "import nltk; nltk.download('punkt'); nltk.download('punkt_tab')"
```

### Visualization Errors

Ensure seaborn, matplotlib, and pandas are installed:

```bash
pip install seaborn matplotlib pandas
```

---

## Performance Reference

### Phase 1 — Document Generation

| Backend | Speed/Doc | Quality | Memory |
|---|---|---|---|
| Groq llama3-8b | ~2-3s | High | 0 (API) |
| Groq llama3.3-70b | ~3-6s | Very High | 0 (API) |
| qwen2.5:7b (Ollama CPU) | ~30+ min | High | ~6 GB |

### Phase 1.5 — Model Comparison (per document, `fast` preset)

| Model | Tier | Latency | Fact Fidelity |
|---|---|---|---|
| Llama 3.1 8B (Groq) | A-Baseline | ~1-2s | 0.75 |
| Llama 3.3 70B (Groq) | A-Baseline | ~2-4s | 0.875 |
| Gemma 4 E4B (local) | B-Small | ~10-15s | 0.625-0.875 |
| Qwen 3.5 4B (local) | B-Small | ~12-25s | 0.75-0.875 |
| Gemma 4 31B Cloud | C-LargeCloud | ~15-25s | 0.75-1.0 |
| Qwen 3.5 397B MoE Cloud | C-LargeCloud | ~8-15s | 0.0-0.75 |

*Latency varies with Ollama server load for cloud models.*
