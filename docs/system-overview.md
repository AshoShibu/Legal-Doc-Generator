# Maharashtra Legal Document Generation System — First Principles Overview

## The Problem

A Maharashtra lawyer preparing a Sale Deed must simultaneously:
- Cross-reference the Transfer of Property Act 1882 (sections 54–57)
- Verify compulsory registration under the Registration Act 1908 (section 17)
- Apply stamp duty under Maharashtra Stamp Act 1958 (Article 25, Schedule I)
- Check mutation obligations under MLRC 1966 (sections 149–153)
- Cite any relevant Bombay High Court precedents

Then type every clause from scratch, in the correct Maharashtra drafting format, without hallucinating a section number that does not exist.

This project automates that process end-to-end: from a scanned land record image to a downloadable, citation-grounded legal draft.

---

## Why CAG, Not RAG

### The Standard RAG Approach

Retrieval-Augmented Generation (RAG) works by embedding a large corpus into a vector store, then at query time retrieving the top-k most semantically similar chunks and injecting them into the LLM prompt. This is powerful for open-domain question answering where the relevant documents are unknown in advance.

### Why RAG Alone Is Insufficient Here

Maharashtra legal drafting has a fundamentally different structure:

1. **The relevant statute set is small and known in advance.** A Sale Deed always needs TPA 1882, Registration Act 1908, and Maharashtra Stamp Act 1958. A Leave and License Agreement always needs Maharashtra Rent Control Act 1999 Section 24. There is no retrieval uncertainty — the documents are predetermined by document type.

2. **Retrieval introduces hallucination risk.** If the vector store returns a chunk from a different state's rent control act (because it is semantically similar), the LLM will cite it. In legal drafting, a wrong jurisdiction citation is worse than no citation.

3. **Latency matters for a live UI.** A full-corpus FAISS search over 531 Maharashtra Acts adds 2–5 seconds of retrieval latency before generation even begins. For a 7-slot document, this compounds.

4. **Context window is large enough.** Modern LLMs (Groq's llama-3.3-70b-versatile) have 128,000-token context windows. The entire relevant statute set for a Sale Deed fits in ~15,000 tokens — well within budget.

### Cache-Augmented Generation (CAG)

CAG pre-loads the relevant statutes into the LLM context window **before** generation begins, as a fixed **Jurisdictional Legal Cache**. The LLM is then constrained to cite only what is in the cache. Any clause it cannot ground produces an explicit `[UNGROUNDED — MANUAL REVIEW REQUIRED]` marker rather than a hallucinated citation.

```
RAG:  Query → Embed → Retrieve top-k chunks → Inject → Generate
CAG:  Document type selected → Load fixed cache → Inject → Generate
```

The cache is curated per document type via YAML manifests in `config/cache_manifests/`. Each manifest lists the statutes in priority order with token counts and `section_hint` fields that extract only the relevant sections (e.g., `section_hint: "54-57"` for TPA sale provisions).

### How This System Combines CAG and RAG Principles

This is not pure CAG. The system uses CAG for Phase 1 (the primary deliverable) and is architected to support RAG comparison in Phase 2:

| Aspect | This System's Approach |
|---|---|
| Cache construction | CAG — YAML manifests, curated per doc type |
| Section extraction | RAG-like — `section_hint` extracts only relevant sections from each statute, mimicking chunk retrieval |
| Citation grounding | CAG — every citation verified against the loaded cache |
| Token budget enforcement | CAG — 90% of context window; lower-priority entries dropped first |
| Phase 2 (optional) | Full RAG — Dense (FAISS/bge-m3), Hybrid (dense + BM25), Agentic (LangGraph multi-agent) |

The `section_hint` mechanism is the key bridge: rather than loading entire acts (which would waste context), the cache loader extracts only the sections relevant to the document type — functionally equivalent to a retrieval step, but deterministic and jurisdiction-safe.

---

## Why Multi-Agent Calls for Quality Drafting

### The Single-Pass Problem

A naive approach asks the LLM: "Write a complete Sale Deed using these statutes and these facts." This fails for 8B–70B parameter models because:

1. **Competing objectives.** The model must simultaneously recall statute sections, structure a multi-section document, inject intake field values, and format citations — too many tasks for one generation call.
2. **Context dilution.** With 3,000 characters of legal cache plus a full document prompt, the model's attention is spread thin. Early sections get good attention; later sections degrade.
3. **Slot abandonment.** The model often outputs bare section names (`PARTIES_CLAUSE`, `ATTESTATION`) as placeholders rather than generating content for them.

### Two-Pass Generation (the "Multi-Agent" Architecture)

The system uses a **two-pass generation** approach that functions like a multi-agent pipeline:

**Pass 1 — Per-slot focused LLM calls (`_build_slot_prompt` in `engine.py`)**

Each template slot gets its own dedicated LLM call with a narrow, specific prompt:

```
"Write ONLY the PARTIES section of a Sale Deed.
 SECTION TO WRITE: the opening clause identifying the Vendor (Seller) and Purchaser (Buyer)
 — use the exact seller_name, seller_address, buyer_name, buyer_address from the FACTS..."
```

This is functionally equivalent to a specialized agent for each document section. The model has one job per call: write 100–300 tokens of focused legal text for a single section. This eliminates the competing-objectives problem.

**Pass 2 — Deterministic assembly (`_assemble_from_slots` in `engine.py`)**

The slot outputs are stitched together deterministically with clean section headings. No LLM is involved in assembly — it is pure string concatenation with post-processing:
- Citation placeholder resolution (`[Legislature/Court]` → `Parliament of India`)
- Act-only citation detection (citations missing a section number → `[UNGROUNDED]`)

**Per-slot retry (quality gate)**

If a slot returns fewer tokens than the minimum threshold for that slot type (`_MIN_SLOT_TOKENS_BY_TYPE` in `evaluator.py`), the engine retries once with an explicit minimum-word instruction appended to the prompt. This is the equivalent of an agent supervisor checking output quality and requesting a redo.

**Post-generation quality gate (`app.py`)**

After the full document is assembled, a quick Tier 1+2 evaluation runs. If `cache_hit_rate < 0.6` or `section_completeness < 0.8`, the entire generation retries once — reusing the same `LegalCache` object (no reload, per Requirement 4.8).

This architecture — multiple focused calls + deterministic assembly + quality-gated retry — is why the system produces structurally complete documents with correct citations rather than the generic boilerplate a single-pass approach produces.

---

## Complete File-by-File Workflow

### Stage 0 — Configuration and Environment

**`.env` / `config/settings.py`**

All runtime configuration is loaded from environment variables with typed defaults. Key variables:
- `GROQ_API_KEY` — Groq cloud API key for LLM generation
- `GROQ_RAGAS_API_KEY` — separate key for RAGAS evaluation (independent rate limit pool)
- `OLLAMA_BASE_URL` — local Ollama server URL (fallback for offline use)
- `LLM_DEFAULT_BACKEND` — default model (`groq_llama3_8b`)
- `OUTPUT_DIR` — where generated documents are written
- `CPU_ONLY_MODE` — restricts to smaller models on CPU-only machines

`config/settings.py` exposes a module-level `settings` singleton (a typed dataclass) that any module can import.

---

### Stage 1 — Data Ingestion: OCR Pipeline

**`src/ocr/pipeline.py`** — entry point: `extract(file_path) -> OCRResult`

**`src/ocr/marathi_map.py`** — Marathi→English field label normalisation

The user uploads a scanned land record (7/12 Extract or Property Card). The OCR pipeline:

1. **Text-layer detection** — PyMuPDF (`fitz`) opens the PDF. If it has a text layer, `_extract_text_layer_pdf()` extracts it directly (fast, lossless, 0.95 confidence).

2. **Scanned image path** — If no text layer, `_extract_via_deepseek_ocr()` sends the image to DeepSeek OCR via the Ollama vision API. The model returns a JSON object of field→value pairs with confidence scores.

3. **Marathi normalisation** — `normalize_fields()` applies the bilingual mapping from `config/marathi_field_map.json` (35 entries). "सर्वे नंबर" → "Survey_Number", "मालकाचे नाव" → "owner_name", etc.

4. **DPI check** — PIL reads the image DPI. If below 150 DPI, a warning is added listing affected fields.

5. **Mandatory field validation** — If `Survey_Number`, `owner_name`, or `area` are missing, descriptive errors are returned.

Returns: `OCRResult(fields, confidence, source_ref, warnings, errors)`

---

### Stage 2 — Privacy: PII Redaction

**`src/pii/redactor.py`** — entry point: `redact(text, structured_json) -> RedactionResult`

Before any text reaches the LLM, PII is stripped:

1. **Survey/Gat number tokenisation** — values from `structured_json` are replaced with `<SURVEY_NUMBER_TOKEN>` in the text. The original values are preserved in `structured_json` (the lawyer's document will have real numbers; the LLM prompt does not).

2. **Regex patterns** — four compiled patterns run in sequence:
   - Aadhaar: `\b\d{4}\s?\d{4}\s?\d{4}\b`
   - PAN: `\b[A-Z]{5}[0-9]{4}[A-Z]\b`
   - Mobile: `\b[6-9]\d{9}\b`
   - Email: RFC 5322 simplified

3. **spaCy NER** — `en_core_web_sm` detects PERSON entities. Confidence ≥ 0.80 → replaced with `<PETITIONER_1>`, `<RESPONDENT_1>`, etc. Confidence < 0.80 → flagged for human review but not redacted.

4. **Audit log** — every redaction is recorded: entity type, placeholder, character offset, confidence. The lawyer can use this to find-and-replace placeholders with real names in the final document.

Returns: `RedactionResult(redacted_text, structured_json, audit_log)`

---

### Stage 3 — Guided Intake Form

**`config/intake_schemas/{doc_type}.json`** — one JSON schema per document type (7 schemas)

**`src/intake/form_renderer.py`** — entry point: `render_form(schema, ocr_fields) -> dict | None`

**`src/intake/fact_pattern_builder.py`** — entry point: `build(schema, ocr_result, intake_answers, redaction_result) -> dict`

OCR extraction alone produces a sparse fact pattern (survey number, owner name, area). The intake form collects the document-specific facts the LLM needs:

- Sale Deed: consideration amount, payment mode, advance paid, witnesses
- Mortgage Deed: loan amount, interest rate, repayment period, mortgage type
- Power of Attorney: powers granted, POA type (General/Special), revocation conditions
- Leave and License: monthly fee, security deposit, lock-in period, permitted use
- Gift Deed: donee acceptance, possession delivery date, conditional gift terms
- Conveyance Deed: MahaRERA number, OC status, society address, flat details
- Affidavit: numbered statements, notarisation authority, place of execution

Each schema field has an `ocr_source` key — if set, the form pre-populates the widget from `OCRResult.fields[ocr_source]`. The user can override.

`build()` merges OCR fields + intake answers into an enriched fact pattern dict. It formats:
- Indian currency: `3200000` → `₹32,00,000 (Rupees Thirty-Two Lakhs only)`
- Dates: `2026-03-20` → `20 March 2026`

Names entered in the form are passed through `PII_Redactor` on submission — the LLM never sees real names.

Returns: `dict` with keys `"intake"` and `"ocr_fields"` — the enriched fact pattern.

---

### Stage 4 — Legal Cache Construction

**`config/cache_manifests/{doc_type}.yaml`** — one YAML manifest per document type (7 manifests)

**`src/cag/cache_loader.py`** — entry point: `load_manifest(document_type, llm_backend) -> ManifestLoadResult`

Each YAML manifest lists the statutes for that document type in priority order:

```yaml
document_type: sale_deed
priority_order:
  - name: Transfer of Property Act, 1882
    path: Maharashtra Legal Document Dataset/Maharashtra Legal Corpus/...
    tokens: 1000
    section_hint: "54-57"
  - name: Maharashtra Stamp Act, 1958
    path: ...
    tokens: 500
    section_hint: "Article 25, Schedule I"
    priority: high
  - name: BHC Judgment - Sale Deed Validity 2023
    path: ...
    tokens: 3200
    priority: low
```

The loader:
1. Reads the YAML and resolves file paths (absolute → cwd-relative → DATASET_ROOT-relative)
2. Enforces the **90% token budget**: normal-priority entries are loaded first; low-priority entries fill remaining budget; any entry that would overflow is omitted and logged
3. Applies `section_hint`: `_extract_section_range()` finds "Section 54" through "Section 57" in the TPA text and returns only that slice — reducing noise in the context window
4. Loads file content: `.docx` via python-docx, `.pdf` via PyMuPDF, `.txt` directly

Returns: `ManifestLoadResult(entries, omitted, total_tokens, budget_tokens)`

---

### Stage 5 — CAG Engine: Two-Pass Generation

**`src/cag/engine.py`** — entry points: `load_cache()`, `generate_draft()`

**`load_cache(document_type, llm_backend) -> LegalCache`**

Calls `load_manifest()`, wraps the result in a `LegalCache` dataclass with a UUID `session_id`, registers it in the in-memory session registry (`_session_cache`), and writes a session log entry to `output/session_log.jsonl`. The same `LegalCache` object is reused for all regenerations within a session — no reload.

**`generate_draft(fact_pattern, cache, doc_type, template_slots, slot_progress_cb) -> DraftResult`**

This is the core generation function. With `template_slots` provided (the normal path from the frontend):

**Pass 1 — Per-slot LLM calls:**

For each slot in `template_slots` (e.g., `["PARTIES_CLAUSE", "RECITALS", "OPERATIVE_CLAUSE_1", ...]`):

1. `_build_cache_context()` assembles the loaded statute text into a single context block (3,000 chars for Groq, 24,000 for Ollama). Truncation is section-boundary-aware — cuts at the last `Section N` header rather than mid-sentence.

2. `_build_slot_prompt()` constructs a focused prompt for this specific slot. It uses document-type-specific descriptions from `_PARTIES_BY_DOC`, `_RECITALS_BY_DOC`, `_OPERATIVE_BY_DOC`, `_ATTESTATION_BY_DOC` — each tailored to inject the right intake fields and cite the right statutes.

3. The LLM is called: `_call_groq()` (Groq cloud, primary) or `_call_ollama()` (local, fallback). Groq uses exponential backoff (2s→4s→8s) on HTTP 429 rate limit responses.

4. **Per-slot retry**: if the response is below `_MIN_SLOT_TOKENS_BY_TYPE[slot_name]` tokens, the engine retries once with an explicit minimum-word instruction.

5. `slot_progress_cb(slot_index, slot_name)` fires before each call — the frontend uses this to stream live updates to `st.status()`.

**Pass 2 — Deterministic assembly:**

`_assemble_from_slots()` stitches slot outputs together with clean section headings. Then:
- `_resolve_citation_placeholders()` replaces any `[Legislature/Court]` / `[Year]` tokens the LLM left behind, using the cache manifest metadata to determine the correct court and year
- `_CITATION_NO_SECTION_RE` catches act-only citations (no section number) and marks them `[UNGROUNDED]`

`_extract_citations()` parses the assembled text for `[Act Name, Year] Section X(Y), Court, Year` patterns and verifies each against the loaded cache. Unverifiable citations are added to `ungrounded_clauses`.

Returns: `DraftResult(content, citations, ungrounded_clauses, session_id)`

---

### Stage 6 — Document Assembly and Citation Verification

**`src/generation/template_mapper.py`** — `TemplateRegistry`, `parse_template()`

**`src/generation/citation_verifier.py`** — `verify_citations()`

**`src/generation/document_generator.py`** — `generate_document() -> GeneratedDocument`

`generate_document()` takes the raw LLM output and:

1. **Template mapping** — `TemplateRegistry` loads `.docx` templates from `Legal Document Templates/`, extracts `{{SLOT_NAME}}` placeholders, and maps LLM-generated content to the correct structural positions. Marathi sections are preserved verbatim.

2. **Citation verification** — every inline citation is checked against the loaded `LegalCache`. Citations not traceable to any cache entry are replaced with `[UNGROUNDED — MANUAL REVIEW REQUIRED]`.

3. **High hallucination risk flag** — if more than 10% of clauses are ungrounded, the document header is flagged "High Hallucination Risk".

4. **Document header** — prepended to every draft:
   ```
   GENERATED BY: Maharashtra Legal Document Generation System
   Pipeline: CAG | Model: groq_llama3_8b | Cache: session:abc12345
   Run ID: {uuid}
   ⚠ DISCLAIMER: AI-generated. Requires review by a qualified legal professional.
   ```

5. **Citation index** — appended to the end of every document, listing all cited sources with full bibliographic details and the clause numbers they support.

Returns: `GeneratedDocument(content, header, body, citation_index, citations, ungrounded_clauses, high_hallucination_risk, run_id)`

---

### Stage 7 — Export

**`src/generation/exporter.py`** — `export_document() -> ExportResult`

Two formats are produced:

- **DOCX** — `python-docx` writes the document with citation markers as Word bookmarks. The lawyer can open this in Microsoft Word and edit directly.
- **PDF** — `reportlab` renders the document with clickable citation anchors. Each `[1]`, `[2]` marker links to the citation index at the end.

Both formats include the document header and citation index.

Files are written to `output/{pipeline_variant}/{doc_type}/{run_id}/`.

Returns: `ExportResult(docx_path, pdf_path, errors)`

---

### Stage 8 — Evaluation (Automatic, Every Generation)

**`src/evaluation/evaluator.py`** — `evaluate_cag_document() -> CAGEvaluationResult`

After every generation, three tiers of metrics are computed automatically:

**Tier 1 — CAG-specific (no external dependencies):**
- `cache_hit_rate` — grounded citations / total citations. The defining CAG metric.
- `slot_fill_rate` — slots with ≥ `_MIN_SLOT_TOKENS_BY_TYPE[slot]` tokens of real content / total slots. Per-slot thresholds: PARTIES=20, ATTESTATION=15, RECITALS=25, SCHEDULE=30, OPERATIVE=50.
- `fact_fidelity_score` — intake field values found in the output. Includes Indian lakh/crore numeric normalisation (raw int `3200000` matches formatted `32,00,000`).
- `latency_seconds` — wall-clock generation time.

**Tier 2 — Structural/legal quality (regex-based):**
- `section_completeness` — expected headings (PARTIES, RECITALS, OPERATIVE CLAUSE, SCHEDULE, ATTESTATION) present in output.
- `citation_format_compliance` — citations matching `[Act, Year] Section X(Y), Court, Year`.
- `jurisdictional_accuracy` — cited acts belonging to known Maharashtra/Central acts list.

**Tier 3 — RAGAS (LLM-judged, for research paper):**
- `ragas_faithfulness` — claims in output supported by cache context (Groq llama-3.3-70b-versatile as judge, all-MiniLM-L6-v2 embeddings locally).
- `ragas_answer_relevance`, `ragas_context_precision`, `ragas_context_recall`.

The Tier 1+2 results appear automatically in the Streamlit metrics panel after every generation. RAGAS runs separately via `scripts/test_ragas_integration.py` (requires `GROQ_RAGAS_API_KEY`).

---

### Stage 9 — Run Logging

**`src/logging/run_logger.py`** — `log_run()`, `get_run()`, `store_output()`

Every generation is logged to a SQLite database at `output/runs.db`:

```sql
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    timestamp TEXT,
    pipeline_variant TEXT,      -- "cag"
    llm_model TEXT,             -- "groq_llama3_8b"
    input_hash TEXT,            -- SHA-256 of uploaded file
    cache_composition TEXT,     -- JSON: [{name, tokens}, ...]
    output_hash TEXT,           -- SHA-256 of DOCX output
    output_path TEXT,           -- output/cag/sale_deed/{run_id}/
    status TEXT                 -- "success" | "error" | "partial"
);
```

Entries older than 30 days are auto-purged. `get_run(run_id)` retrieves the full log entry for reproducibility.

---

### Stage 10 — Streamlit Frontend

**`src/frontend/app.py`** — the complete user interface

The frontend is a single-page Streamlit application with a state machine:

```
idle → intake → running_generation → done | error
         ↑
    (upload flow adds: running_ocr → intake)
```

**Sidebar** — document type selector (7 types), pipeline mode (CAG / Phase 2 stubs), LLM backend selector.

**Input methods:**
- *Fill Questionnaire* — skips OCR, goes directly to the intake form
- *Upload Land Record* — runs OCR phase first, pre-fills intake form

**Generation flow** — uses `st.status()` for live streaming updates to the browser. Each `st.write()` call inside the status context renders immediately (server-sent events), so the user sees a live log:
```
✓ Building fact pattern...
✓ Loading legal cache...
✓ Starting LLM generation (7 sections)...
✓ Generating Parties Clause... (1/7)
✓ Generating Recitals... (2/7)
...
✓ Verifying citations...
✓ Running quality evaluation...
✓ Exporting DOCX and PDF...
Document generated successfully. ✓
```

**Draft preview** — scrollable rendered draft with `[1]`, `[2]` citation markers as clickable links. Clicking opens a citation side panel showing the source act, section, year.

**Download buttons** — DOCX and PDF download buttons appear immediately after generation. If export failed, a caption explains why.

**Quality metrics panel** — collapsible expander showing all Tier 1+2 metrics with delta indicators. Starts collapsed so the draft is visible immediately.

---

### Stage 11 — Local Deployment

**`Dockerfile`** and **`docker-compose.yml`**

The system runs as two Docker services:

```yaml
services:
  app:
    build: .
    ports: ["8501:8501"]   # Streamlit frontend
    env_file: .env
  ollama:
    image: ollama/ollama
    ports: ["8000:11434"]  # LLM inference API
```

`docker-compose up -d` starts both services. The Streamlit app is accessible at `http://localhost:8501`.

For cloud deployment (AWS EC2 / GCP Compute Engine):
1. Set `GROQ_API_KEY` and `GROQ_RAGAS_API_KEY` in `.env`
2. Set `CPU_ONLY_MODE=true` if no GPU (restricts to llama3:8b or qwen2:7b)
3. Run `docker-compose up -d`
4. Optionally configure nginx reverse proxy + Let's Encrypt SSL

**Without Docker** (development):
```bash
pip install -r requirements.txt
python -m spacy download en_core_web_sm
streamlit run src/frontend/app.py
```

---

## Data Flow Summary

```
User uploads 7/12 Extract (PDF/image)
        │
        ▼
src/ocr/pipeline.py          PyMuPDF (text-layer) or DeepSeek OCR (scanned)
src/ocr/marathi_map.py       "सर्वे नंबर" → "Survey_Number"
        │  OCRResult
        ▼
src/pii/redactor.py          Aadhaar/PAN/mobile stripped; names → <PETITIONER_1>
        │  RedactionResult
        ▼
config/intake_schemas/       Structured Q&A form (7 schemas, OCR pre-fill)
src/intake/form_renderer.py
src/intake/fact_pattern_builder.py   Merges OCR + intake; formats ₹32,00,000
        │  enriched fact_pattern dict
        ▼
config/cache_manifests/      YAML: which statutes, which sections, token budget
src/cag/cache_loader.py      Loads + section-extracts statute text
        │  LegalCache (session-registered, reused on retry)
        ▼
src/cag/engine.py            Two-pass generation:
  _build_slot_prompt()         Pass 1: one focused LLM call per slot
  _call_groq() / _call_ollama()  Groq (primary) or Ollama (fallback)
  _assemble_from_slots()       Pass 2: deterministic assembly
  _resolve_citation_placeholders()  Post-processing safety net
        │  DraftResult
        ▼
src/generation/document_generator.py   Template mapping + citation verification
src/generation/citation_verifier.py    [UNGROUNDED] markers for unverifiable citations
        │  GeneratedDocument
        ▼
src/generation/exporter.py   DOCX (python-docx) + PDF (reportlab)
        │  ExportResult
        ▼
src/evaluation/evaluator.py  Tier 1+2 metrics (automatic, every generation)
        │  CAGEvaluationResult
        ▼
src/logging/run_logger.py    SQLite: UUID, timestamp, cache composition, hashes
        │
        ▼
src/frontend/app.py          Draft preview + citation panel + download buttons
                             + quality metrics panel
```

---

## Key Design Decisions and Their Rationale

| Decision | Rationale |
|---|---|
| CAG over RAG for Phase 1 | Statute set is small and known; no retrieval uncertainty; no vector store dependency; faster deployment |
| Two-pass generation | Single-pass produces slot abandonment and generic boilerplate; focused per-slot calls eliminate competing objectives |
| Groq as primary LLM backend | Free tier, 2–3s per call, 128K context window; no local GPU required for development |
| `section_hint` in manifests | Loads only relevant statute sections, reducing context noise without a full retrieval system |
| Per-slot token threshold retry | Catches short/empty slot outputs before assembly; equivalent to an agent supervisor |
| `st.status()` for progress | Only Streamlit API that streams updates mid-function; `st.progress()` inside a blocking function does not repaint the browser |
| PII redaction before LLM | Names never reach Groq's servers; audit log enables find-and-replace in final document |
| Separate `GROQ_RAGAS_API_KEY` | RAGAS evaluation uses a different rate limit pool from generation; prevents evaluation from blocking document generation |
| SQLite for run logging | No external database dependency; sufficient for prototype; 30-day auto-purge keeps disk usage bounded |
| Indian lakh/crore normalisation in fact fidelity | Raw intake integers (3200000) must match formatted LLM output (₹32,00,000) for accurate fidelity scoring |
