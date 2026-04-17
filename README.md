# Maharashtra Legal Document Generation System

An AI-assisted pipeline for drafting citation-grounded legal documents under Maharashtra jurisdiction. The system extracts structured data from scanned land records, redacts PII, collects document-specific facts through a guided intake form, and generates complete legal documents grounded in Maharashtra statutes.

**Phase 1.5** adds a 3-tier SLM model comparison pipeline, research-paper-quality visualizations, and a conversational chat backend for document intake.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Phase 1.5 Enhancements](#phase-15-enhancements)
3. [Repository Structure](#repository-structure)
4. [Setup and Installation](#setup-and-installation)
5. [Quick Start](#quick-start)
6. [Model Comparison Pipeline](#model-comparison-pipeline)
7. [Using the React Frontend](#using-the-react-frontend)
8. [Running Tests](#running-tests)
9. [Configuration Reference](#configuration-reference)
10. [Cloud Deployment](#cloud-deployment)
11. [Security](#security)

---

## Architecture Overview

```
Upload (PDF/Image)
       |
       v
  OCR Pipeline          <- PyMuPDF (text-layer) / DeepSeek OCR (scanned)
       |
       v
  PII Redactor          <- RegEx (Aadhaar/PAN/mobile/email) + spaCy NER
       |
       v
  Guided Intake Form    <- OCR fields pre-fill; user fills document-specific facts
       |
       v
  Fact Pattern Builder  <- Merges OCR + intake answers; formats currency/dates
       |
       v
  CAG Engine            <- Loads YAML cache manifest -> Groq/Ollama LLM
       |
       v
  Citation Formatter    <- [Phase 1.5] Parses [CITE:...] markers, builds index
       |
       v
  Document Generator    <- Template slots + Citation Verifier
       |
       v
  Formatting Engine     <- [Phase 1.5] Underlines party names (Maharashtra convention)
       |
       v
  Exporter              <- DOCX (python-docx) + PDF (reportlab)
       |
       v
  Run Logger            <- SQLite (UUID, timestamp, cache composition, hashes)
```

---

## Phase 1.5 Enhancements

### Area 1 — Citations and Formatting ✅
- **End-of-document citation index**: LLM outputs `[CITE:Act, Year, Section]` markers; `Citation_Formatter` replaces them with `[N]` and appends a numbered index per Indian Legal Citation Standard
- **Party name underlining**: `Formatting_Engine` detects and underlines party names (Vendor, Purchaser, Mortgagor, etc.) in DOCX and PDF per Maharashtra drafting conventions
- Both features are gated by `ENABLE_CITATION_FORMATTING` and `ENABLE_UNDERLINE_FORMATTING` flags

### Area 2 — SLM Model Comparison ✅
3-tier research pipeline comparing 6 models across 7 document types:

| Tier | Models | Backend |
|---|---|---|
| A — Baseline | Llama 3.1 8B, Llama 3.3 70B | Groq API (free) |
| B — Small Model Viability | Gemma 4 E4B, Qwen 3.5 4B | Ollama local |
| C — Large Cloud | Gemma 4 31B Cloud, Qwen 3.5 397B MoE Cloud | Ollama cloud (free) |

Features:
- 7 evaluation metrics (CHR, SFR, FFid, SecC, CitF, JurA, Latency)
- 5 research-paper-quality seaborn visualizations auto-generated after each run
- `--resume` flag for interrupted runs, `--dry-run` for connectivity checks
- Multi-key Groq API rotation for rate limit resilience
- PDF + JSON document saving per run

### Area 3 — Chat Backend ⏳ (in progress)
Lightweight FastAPI conversational interface for document intake.

---

## Repository Structure

```
.
├── src/
│   ├── ocr/                    # OCR extraction (PyMuPDF + DeepSeek)
│   ├── pii/                    # PII detection and anonymisation
│   ├── intake/                 # Guided intake form + fact pattern builder
│   ├── cag/                    # CAG engine + cache loader
│   ├── generation/
│   │   ├── citation_formatter.py   # [Phase 1.5] Citation index builder
│   │   ├── formatting_engine.py    # [Phase 1.5] Party name underlining
│   │   ├── document_generator.py
│   │   ├── template_mapper.py
│   │   ├── citation_verifier.py
│   │   └── exporter.py
│   ├── model_comparison/       # [Phase 1.5] SLM comparison pipeline
│   │   ├── model_registry.py   # 6-model registry (3 tiers)
│   │   ├── backends.py         # Groq + Ollama backends
│   │   ├── comparator.py       # M × P × D orchestration
│   │   ├── reporter.py         # CSV, JSON, Markdown export
│   │   └── visualizer.py       # 5 research-paper figures
│   ├── logging/                # SQLite run logger
│   └── frontend/               # Shared backend packages
│
├── backend/
│   └── main.py                 # FastAPI API for the React frontend
│
├── frontend/                   # React frontend
│   ├── src/
│   └── public/
│
├── config/
│   ├── intake_schemas/         # 7 JSON schemas (one per document type)
│   ├── cache_manifests/        # 7 YAML manifests (statutes + judgments)
│   ├── settings.py             # Typed env-var loader
│   └── marathi_field_map.json  # Bilingual field mapping
│
├── scripts/
│   ├── run_model_comparison.py # [Phase 1.5] Model comparison CLI
│   ├── run_phase1_workflow.py  # Full Phase 1 pipeline
│   └── generate_docs_individually.py
│
├── tests/
│   ├── generation/             # Citation formatter + formatting engine tests
│   ├── test_model_comparison.py
│   ├── cag/
│   └── intake/
│
├── output/                     # Generated files (gitignored)
│   └── model_comparison/
│       ├── results.csv
│       ├── summary.md
│       ├── documents/          # Per-run PDF + JSON
│       └── visuals/            # 5 PNG figures
│
├── .env.example                # All environment variables with defaults
├── .gitignore
├── .dockerignore
├── Dockerfile                  # FastAPI + React production image
├── Dockerfile.model_comparison # [Phase 1.5] Model comparison runner
├── SECURITY.md
├── requirements.txt
└── COMMANDS.md
```

---

## Setup and Installation

### Prerequisites
- Python 3.11+
- [Ollama](https://ollama.com) for local model inference
- Groq API key (free at [console.groq.com/keys](https://console.groq.com/keys))

### 1. Install dependencies

```bash
pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — add your GROQ_API_KEY at minimum
```

For ephemeral hosting such as free Render, you can also bootstrap the dataset from an archive URL on startup:

```bash
DATASET_ROOT=./runtime-dataset
DATASET_BOOTSTRAP_URL=https://drive.google.com/file/d/<FILE_ID>/view?usp=sharing
DATASET_BOOTSTRAP_ARCHIVE=dataset.zip
```

If the archive contains a top-level `Maharashtra Legal Document Dataset/` folder, `DATASET_ROOT` should point to the parent extraction directory, not the dataset folder itself.

### 3. Pull Ollama models (for Phase 1.5 model comparison)

```bash
# Required for Tier B (small model viability)
ollama pull gemma4:e4b
ollama pull qwen3.5:4b

# Tier C cloud models — no pull needed, connect on first run
# ollama run gemma4:31b-cloud
# ollama run qwen3.5:397b-cloud

# Ollama login required for cloud models
ollama login
```

---

## Quick Start

### Run the FastAPI backend

```bash
uvicorn backend.main:app --reload
```

Access at: http://localhost:8000

### Run the React frontend

```bash
cd frontend
npm install
npm start
```

Access at: http://localhost:3000

### Run the Model Comparison Pipeline (Phase 1.5)

```bash
# Validate connectivity before a long run
python scripts/run_model_comparison.py --models all --dry-run

# Run all 6 models on sale deed
python scripts/run_model_comparison.py --models all --doc-types sale_deed

# Run all 6 models on all 7 document types
python scripts/run_model_comparison.py --models all --doc-types all

# Resume an interrupted run
python scripts/run_model_comparison.py --models all --doc-types all --resume

# Cloud deployment with JSON logs
python scripts/run_model_comparison.py --models all --doc-types all --json-logs
```

Results saved to `output/model_comparison/`:
- `results.csv` — one row per run, all 7 metrics
- `summary.md` — Markdown report with tier comparison tables
- `documents/` — PDF + JSON per generated document
- `visuals/` — 5 research-paper figures (heatmap, latency, radar, bars, distribution)

---

## Model Comparison Pipeline

### 3-Tier Design

The comparison is structured as a deliberate research experiment:

- **Tier A (Baseline)**: Llama 3.1 8B and 3.3 70B via Groq — establishes quality ceiling
- **Tier B (Small Model Viability)**: Gemma 4 E4B and Qwen 3.5 4B locally — tests whether 4B models are viable for legal drafting
- **Tier C (Large Cloud Viability)**: Gemma 4 31B Cloud and Qwen 3.5 397B MoE Cloud via Ollama — tests flagship-class quality at zero local RAM cost

### Evaluation Metrics

| Metric | Description |
|---|---|
| CHR | Cache hit rate — fraction of cache content used |
| SFR | Slot fill rate — fraction of template slots populated |
| FFid | Fact fidelity — fraction of input facts present in output |
| SecC | Section completeness — fraction of expected sections present |
| CitF | Citation format compliance |
| JurA | Jurisdictional accuracy — citations reference known Maharashtra/Central Acts |
| Lat(s) | Generation latency in seconds |

### CLI Reference

```bash
python scripts/run_model_comparison.py \
  --models all                    # or specific keys: llama-3.1-8b gemma-4-e4b
  --presets high_quality fast     # or just one preset
  --doc-types all                 # or specific: sale_deed conveyance_deed
  --output-dir ./output/mc        # default: output/model_comparison
  --include-large-models          # include optional models (qwen-2.5-7b etc.)
  --resume                        # skip already-completed combinations
  --dry-run                       # validate API keys + Ollama before running
  --json-logs                     # emit JSON logs for CloudWatch/Stackdriver
```

### Groq Rate Limit Handling

Add multiple free Groq API keys to `.env` for automatic rotation:

```bash
GROQ_API_KEY=gsk_...
GROQ_API_KEY_2=gsk_...
GROQ_API_KEY_3=gsk_...
GROQ_API_KEY_4=gsk_...
```

The backend rotates keys on HTTP 429 and waits for quota refresh when all keys are exhausted.

---

## Using the React Frontend

1. **Upload** a scanned land record (PDF, JPEG, PNG, TIFF)
2. **Select** document type (Sale Deed, Mortgage Deed, Power of Attorney, Leave and License, Gift Deed, Conveyance Deed, Affidavit)
3. **Review** OCR-extracted fields and fill in the guided intake form
4. **Generate** — the pipeline runs OCR → PII redaction → CAG → citation formatting → export
5. **Download** DOCX or PDF with citation index and underlined party names

---

## Running Tests

```bash
# All tests
pytest -v

# Phase 1 tests
pytest tests/cag/ tests/intake/ tests/generation/ -v

# Phase 1.5 — citation formatter (31 tests)
pytest tests/generation/test_citation_formatter.py -v

# Phase 1.5 — formatting engine (34 tests)
pytest tests/generation/test_formatting_engine.py -v

# Phase 1.5 — model comparison (mocked, no API keys needed)
pytest tests/test_model_comparison.py -v

# With coverage
pytest --cov=src --cov-report=html
```

---

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `GROQ_API_KEY` | — | Groq API key (required for Tier A models) |
| `GROQ_API_KEY_2..4` | — | Additional keys for rate limit rotation |
| `GROQ_RAGAS_API_KEY` | — | Groq key for RAGAS Tier 3 evaluation |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API endpoint |
| `ENABLE_PHASE_1_5` | `true` | Master switch for all Phase 1.5 features |
| `ENABLE_CITATION_FORMATTING` | `true` | End-of-document citation index |
| `ENABLE_UNDERLINE_FORMATTING` | `true` | Party name underlining |
| `ENABLE_MODEL_COMPARISON` | `true` | Model comparison CLI |
| `ENABLE_CHAT_BACKEND` | `true` | Chat backend API |
| `MODEL_COMPARISON_OUTPUT_DIR` | `./output/model_comparison` | Results directory |
| `INCLUDE_LARGE_MODELS` | `false` | Include optional 70B+ models |
| `DATASET_ROOT` | `./Maharashtra Legal Document Dataset` | Legal corpus root |
| `OUTPUT_DIR` | `./output` | Generated documents root |
| `LOG_RETENTION_DAYS` | `30` | Auto-purge threshold |

See `.env.example` for the full list with descriptions.

---

## Cloud Deployment

### FastAPI + React App

```bash
docker build -t maharashtra-legal .
docker run -p 8000:8000 --env-file .env maharashtra-legal
```

### Model Comparison (batch job)

```bash
docker build -f Dockerfile.model_comparison -t maharashtra-model-comparison .

# Groq-only (no local Ollama needed)
docker run --rm \
  -e GROQ_API_KEY=gsk_... \
  -v $(pwd)/output:/app/output \
  maharashtra-model-comparison \
  --models llama-3.1-8b llama-3.3-70b --doc-types all --json-logs

# Full pipeline (requires Ollama on host)
docker run --rm \
  --network host \
  -e GROQ_API_KEY=gsk_... \
  -v $(pwd)/output:/app/output \
  maharashtra-model-comparison \
  --models all --doc-types all --json-logs
```

---

## Security

See [SECURITY.md](SECURITY.md) for the full security policy.

- Never commit `.env` — it is gitignored
- Rotate Groq API keys regularly at [console.groq.com/keys](https://console.groq.com/keys)
- In production, inject secrets via your cloud provider's secret manager (AWS Secrets Manager, GCP Secret Manager)
- Rate limiting for the chat backend should be configured at the load balancer level (AWS ALB, GCP Cloud Armor, Nginx)
