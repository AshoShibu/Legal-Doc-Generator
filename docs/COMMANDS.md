# Maharashtra Legal Document Generation — Command Reference

Quick reference for all commands to run, test, and evaluate the system.

---

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

Create `.env` file with your Groq API key:

```bash
GROQ_API_KEY=your_groq_api_key_here
OLLAMA_BASE_URL=http://localhost:11434
OUTPUT_DIR=./output
```

Get your Groq API key at: https://console.groq.com/keys

### 3. Download spaCy Model (for PII detection)

```bash
python -m spacy download en_core_web_sm
```

---

## Running the System

### React + FastAPI Local Deployment:

```bash
uvicorn backend.main:app --reload
cd frontend
npm install
npm start
```

### Full Pipeline (All 7 Document Types)

Runs OCR → PII Redaction → CAG → Document Generation → Export for all 7 document types:

```bash
python scripts/run_phase1_workflow.py
```

Output: `output/cag/{doc_type}/{run_id}/`

### Individual Document Generation (5 Documents via Groq)

Generates 5 documents as separate Groq API requests (mortgage, leave & license, gift, conveyance, affidavit):

```bash
python scripts/generate_docs_individually.py
```

Output: `output/individual/{doc_type}/{run_id}/`

Each document generates:
- `{doc_type}.txt` — plain text draft
- `{run_id}.docx` — Word document
- `{run_id}.pdf` — PDF export

---

## Testing

### Run All Tests

```bash
pytest
```

### Run Specific Test Suites

```bash
# OCR tests
pytest tests/ocr/ -v

# PII redaction tests
pytest tests/pii/ -v

# CAG engine tests
pytest tests/cag/ -v

# Document generation tests
pytest tests/generation/ -v

# Integration tests
pytest tests/test_cag_integration.py -v

# Property-based tests (correctness properties)
pytest tests/cag/test_cag_properties.py -v
pytest tests/generation/test_generation_properties.py -v
```

### Run Tests with Coverage

```bash
pytest --cov=src --cov-report=html
```

View coverage report: `htmlcov/index.html`

---

## Evaluation

### Evaluate a Single Generated PDF (BLEU, ROUGE, Tier 1 + Tier 2)

```bash
python -W ignore scripts/evaluate_single_pdf.py "path/to/generated.pdf"
```

Output: `output/evaluation_results.json`

### Run RAGAS Tier 3 Validation (standalone)

```bash
python -W ignore scripts/test_ragas_integration.py
```

Requires `GROQ_RAGAS_API_KEY` in `.env`. Output: `output/ragas_test_result.json`

### Quality Iteration — Establish Baselines for All 7 Document Types

Run this first before starting the quality iterat

---

## Frontend (React)

### Launch React Frontend with API

```bash
uvicorn backend.main:app --reload
cd frontend
npm install
npm start
```

API: http://localhost:8000
UI: http://localhost:3000

Features:
- Upload scanned legal documents (PDF/images)
- OCR extraction
- PII redaction
- Guided intake forms (7 document types)
- CAG-powered document generation
- DOCX/PDF export

---

## Ollama (Local LLM — Optional)

If you want to use local models instead of Groq:

### 1. Install Ollama

Download from: https://ollama.ai/download

### 2. Pull Models

```bash
# Fast local model (1.1B params, ~640 MB)
ollama pull tinyllama:1.1b

# Better quality (1.5B params, ~900 MB)
ollama pull qwen2.5:1.5b

# High quality (7B params, ~4.7 GB)
ollama pull qwen2.5:7b
```

### 3. Update Scripts

Change `LLM_BACKEND` in scripts:

```python
# scripts/run_phase1_workflow.py
LLM_BACKEND = "qwen2.5_1.5b"  # or "tinyllama_1.1b", "qwen2.5_7b"
```

---

## Output Structure

```
output/
├── cag/                          # Full pipeline outputs
│   ├── sale_deed/{run_id}/
│   ├── mortgage_deed/{run_id}/
│   └── ...
├── individual/                   # Individual Groq-generated docs
│   ├── mortgage_deed/{run_id}/
│   │   ├── mortgage_deed.txt
│   │   ├── {run_id}.docx
│   │   └── {run_id}.pdf
│   └── ...
├── evaluation_results.txt        # BLEU/ROUGE evaluation report
├── evaluation_results.json       # Machine-readable scores
├── runs.db                       # SQLite run log
└── session_log.jsonl             # CAG session log
```

---

## Troubleshooting

### Groq API Rate Limits

Two-pass generation makes one Groq call per template slot (typically 7 calls). The engine handles rate limits automatically:

- **Retry-After header**: if Groq returns a 429 with a `Retry-After` header, the engine waits that duration before retrying
- **Exponential backoff**: if no header, waits 2s → 4s → 8s (up to 3 retries)
- **Inter-slot delay**: a 2s pause between slot calls keeps burst rate under the free-tier limit

If you still hit persistent 429s (e.g. shared API key with high traffic), switch to a local Ollama model:

```python
LLM_BACKEND = "qwen2.5_1.5b"
```

### Out of Memory (OOM) with Large Models

Use smaller models:

```bash
ollama pull tinyllama:1.1b
```

Then set `LLM_BACKEND = "tinyllama_1.1b"`

### Slow Inference on CPU

Groq cloud API is fastest (2-3s per document). Local models on CPU:
- `tinyllama:1.1b` — ~6 min/doc
- `qwen2.5:1.5b` — ~10 min/doc
- `qwen2.5:7b` — ~30+ min/doc

### spaCy Model Not Found

```bash
python -m spacy download en_core_web_sm
```

### NLTK Tokenizer Missing

```bash
python -c "import nltk; nltk.download('punkt'); nltk.download('punkt_tab')"
```

---

## Quick Start (Recommended)

1. Install dependencies: `pip install -r requirements.txt`
2. Add Groq API key to `.env`
3. Download spaCy model: `python -m spacy download en_core_web_sm`
4. Generate documents: `python scripts/generate_docs_individually.py`
5. Evaluate: `python scripts/evaluate_documents.py`
6. Run tests: `pytest`
7. Launch UI: `cd frontend && npm start` with `uvicorn backend.main:app --reload` running

---

## Performance Benchmarks

| Backend            | Speed/Doc (single-pass) | Speed/Doc (two-pass, 7 slots) | Quality | Memory  |
|--------------------|-------------------------|-------------------------------|---------|---------|
| Groq llama3-8b     | ~3s                     | ~30s (incl. 2s inter-slot delays)  | High    | 0 (API) |
| tinyllama:1.1b     | ~6 min                  | ~42 min                       | Fair    | ~1 GB   |
| qwen2.5:1.5b       | ~10 min                 | ~70 min                       | Good    | ~2 GB   |
| qwen2.5:7b         | ~30+ min                | ~3.5 hrs (CPU) / ~14 min (GPU)| High    | ~6 GB   |

Groq is recommended for development and testing. Two-pass generation is the default
when using the React frontend or backend export flow (template slots are always resolved). Single-pass
is used when calling `generate_draft()` directly from scripts without `template_slots`.
