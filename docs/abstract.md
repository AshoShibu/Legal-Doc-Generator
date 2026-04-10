# Abstract — Maharashtra Legal Document Generation System

## Problem

Legal drafting in Maharashtra is manual, slow, and error-prone. A lawyer preparing a Sale Deed or Mortgage Deed must cross-reference the Transfer of Property Act 1882, the Registration Act 1908, the Maharashtra Land Revenue Code 1966, and recent Bombay High Court precedents — then type every clause from scratch. Scanned land records (7/12 Extracts, Property Cards) arrive as images with Marathi field labels. The gap between a raw land record and a court-ready deed is entirely human labour.

This project automates that gap using an AI pipeline that reads the land record, collects the missing facts through a structured intake form, and generates a citation-grounded legal document — without hallucinating statutes that do not exist.

---

## Approach — Cache-Augmented Generation (CAG)

The core insight is that Maharashtra legal drafting draws from a small, stable set of statutes. Rather than retrieving documents at query time (RAG), we pre-load the relevant statutes into the LLM's context window as a fixed **Jurisdictional Legal Cache** before generation begins. This is Cache-Augmented Generation.

The LLM is then constrained to cite only what is in the cache. Any clause it cannot ground produces an explicit `[UNGROUNDED — MANUAL REVIEW REQUIRED]` marker rather than a hallucinated citation. The cache is curated per document type via YAML manifests — the Sale Deed cache loads the Transfer of Property Act and Registration Act; the Leave and License cache loads the Maharashtra Rent Control Act.

---

## Pipeline — Step by Step

```
Upload (PDF/Image)
    │
    ▼
OCR Extraction          PyMuPDF (text-layer) / DeepSeek OCR (scanned)
    │                   Marathi field labels normalised to English
    ▼
PII Redaction           Aadhaar/PAN/mobile stripped; names → <PETITIONER_1>
    │
    ▼
Guided Intake Form      Structured Q&A per document type (7 schemas)
    │                   OCR fields pre-fill; user fills what OCR cannot
    ▼
Fact Pattern Builder    Merges OCR + intake; formats Indian currency/dates
    │
    ▼
CAG Engine              Loads YAML cache manifest → token-budget enforced
    │                   Two-pass generation:
    │                     Pass 1 — one LLM call per template slot (focused)
    │                     Pass 2 — deterministic assembly with clean headings
    ▼
Document Generator      Template slot mapping + citation verification
    │                   Unverifiable citations replaced with [UNGROUNDED]
    ▼
Exporter                DOCX (python-docx) + PDF (reportlab)
    │                   Clickable citation markers → citation index
    ▼
Run Logger              SQLite — UUID, timestamp, cache composition, hashes
    │
    ▼
Streamlit Frontend      Upload → intake form → preview → download
```

**Why two-pass generation?** A single LLM call asking for a full deed simultaneously requires the model to recall statutes, structure a multi-section document, and insert citations — too many competing objectives for a 8B-parameter model. Two-pass splits this: Pass 1 asks "write the PARTIES section" (narrow, grounded), Pass 2 stitches the results deterministically. This eliminated bare slot names and duplicate clauses from the output.

---

## Evaluation Framework — Three Tiers

### Tier 1 — CAG-Specific (runs on every generation, no external dependencies)

| Metric | What it measures |
|---|---|
| Cache Hit Rate | grounded citations / total citations — the defining CAG metric |
| Slot Fill Rate | template slots with ≥50 tokens of real content / total slots |
| Fact Fidelity Score | intake field values found verbatim in the output |
| Latency (s) | wall-clock time from generation call to DraftResult |

### Tier 2 — Structural/Legal Quality (regex-based, no LLM needed)

| Metric | What it measures |
|---|---|
| Section Completeness | expected headings present in output |
| Citation Format Compliance | citations matching `[Act, Year] Section X(Y), Court, Year` |
| Jurisdictional Accuracy | cited acts belonging to known Maharashtra/Central acts list |

### Tier 3 — RAGAS (LLM-judged, for the research paper)

Uses `ragas 0.4.x` with a Groq `llama-3.3-70b-versatile` judge and local `all-MiniLM-L6-v2` embeddings (no OpenAI key required).

| Metric | What it measures |
|---|---|
| Faithfulness | claims in the output supported by the cache context |
| Answer Relevance | output addresses the fact pattern |
| Context Precision | fraction of the loaded cache actually used |
| Context Recall | cache contained what was needed |

**Faithfulness is the most defensible metric for the paper** — it directly measures the central claim of each pipeline variant.

---

## Baseline Results (Sale Deed, `llama-3.3-70b-versatile`)

| Metric | Score | Interpretation |
|---|---|---|
| Cache Hit Rate | 0.80 | 4 of 5 citations grounded in cache |
| Fact Fidelity | 1.00 | All intake fields present in output |
| Section Completeness | 1.00 | All 5 sections present |
| Citation Format Compliance | fixed (was 0.0) | Two-layer fix: prompt examples + regex post-processing |
| Jurisdictional Accuracy | 1.00 | All citations are Maharashtra/Central acts |
| BLEU avg | 0.25 | Expected low — fact-specific output vs generic reference |
| ROUGE-L F | 0.42 | Moderate overlap with reference template |

---

## Repository Structure

```
.
├── src/
│   ├── ocr/            OCR extraction + Marathi normalisation
│   ├── pii/            PII detection and anonymisation
│   ├── intake/         Guided intake form renderer + fact pattern builder
│   ├── cag/            CAG engine (load_cache, generate_draft, two-pass)
│   ├── generation/     Document assembly, template mapping, citation verifier, exporter
│   ├── evaluation/     Three-tier evaluation framework (Tier 1/2/3 + RAGAS)
│   ├── logging/        SQLite run logger
│   └── frontend/       Streamlit application
├── config/
│   ├── cache_manifests/    One YAML per document type (7 types)
│   ├── intake_schemas/     One JSON schema per document type
│   └── settings.py         Typed env-var loader
├── scripts/
│   ├── evaluate_single_pdf.py      BLEU/ROUGE + Tier 1/2 on a single PDF
│   └── test_ragas_integration.py   Standalone RAGAS Tier 3 validation
├── docs/
│   ├── phase2-implementation-plan.md   Dense/Hybrid/Agentic RAG roadmap
│   └── COMMANDS.md                     Quick-reference commands
├── Maharashtra Legal Document Dataset/ 531 Maharashtra Acts + templates + sample deeds
└── output/             Generated documents, session log, evaluation results
```

---

## Deployment

The system is deployed end-to-end on the cloud (AWS EC2 / GCP Compute Engine) via Docker Compose. Before cloud deployment, every document type passes a quality iteration loop (task 17 in `tasks.md`) that gates on measurable thresholds across all three evaluation tiers plus a manual advocate-review checklist. The Streamlit frontend is publicly accessible on port 8501.

```
docker-compose up -d
# → Streamlit at http://<instance-ip>:8501
```

Key cloud configuration:
- `GROQ_API_KEY` — generation LLM (`llama-3.3-70b-versatile`)
- `GROQ_RAGAS_API_KEY` — RAGAS evaluation judge (separate rate limit pool)
- `CPU_ONLY_MODE=true` — restricts to smaller model variants on CPU instances
- Persistent volume for `output/` — generated documents survive container restarts

## Phase 2 — RAG Comparison (Optional)

Phase 2 implements three additional pipeline variants for the research paper comparison:

- **Dense RAG** — full-corpus FAISS vector search (bge-m3 embeddings), top-10 chunks at query time
- **Hybrid RAG** — weighted dense + BM25 with metadata filters (court type, year, district)
- **Agentic RAG** — LangGraph multi-agent: Issue Agent → Statute Agent ∥ Case Law Agent → Conflict Agent

All four pipelines share the same evaluation framework. The central research question is: **does the fixed CAG cache produce faithfulness scores comparable to dynamic retrieval?** If CAG faithfulness ≥ Dense RAG faithfulness, the pre-loaded cache is not degrading quality — which validates the CAG approach for resource-constrained deployment (no vector store, no embedding model, no retrieval latency).

Full Phase 2 implementation details are in `docs/phase2-implementation-plan.md`.

---

## Key Design Decisions

- **Groq free tier as LLM backend** — `llama-3.3-70b-versatile` for generation and RAGAS judging; `GROQ_API_KEY` for generation, `GROQ_RAGAS_API_KEY` for evaluation (separate rate limit pools)
- **Local embeddings for RAGAS** — `all-MiniLM-L6-v2` via `sentence-transformers`; no OpenAI dependency
- **Citation post-processing safety net** — regex substitution resolves any `[Legislature/Court]` / `[Year]` placeholders the LLM leaves behind, using cache manifest metadata
- **PII never reaches the LLM** — names are replaced with `<PETITIONER_1>` style tokens before prompt injection; the audit log maps tokens back to real names for the lawyer's find-and-replace
- **Evaluation wired into every generation** — Tier 1/2 metrics appear automatically in the Streamlit metrics panel after each run; no manual eval step needed
