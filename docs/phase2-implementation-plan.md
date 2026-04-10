# Phase 2 Implementation Plan
## Maharashtra Legal Document Generation System

Phase 1 (CAG prototype) is complete and running on Streamlit at port 8501.
Phase 2 adds three RAG pipeline variants — Dense, Hybrid, and Agentic — each wired
into the same Streamlit frontend as selectable pipeline modes. The goal is a
side-by-side comparison of all four pipelines (CAG + 3 RAG) for the PBL-2 research paper.

---

## What Already Exists (Phase 1 baseline)

| Module | File | Status |
|---|---|---|
| OCR Pipeline | `src/ocr/pipeline.py` | Done |
| PII Redactor | `src/pii/redactor.py` | Done |
| Guided Intake | `src/intake/` | Done |
| CAG Engine | `src/cag/engine.py` | Done — two-pass generation |
| Document Generator | `src/generation/document_generator.py` | Done |
| Exporter (DOCX/PDF) | `src/generation/exporter.py` | Done |
| Run Logger | `src/logging/run_logger.py` | Done |
| Streamlit Frontend | `src/frontend/app.py` | Done |
| RAG module | `src/rag/__init__.py` | Empty stub |
| Agents module | `src/agents/__init__.py` | Empty stub |
| Evaluation module | `src/evaluation/__init__.py` | Empty stub |

The frontend already has Dense RAG, Hybrid RAG, and Agentic RAG in the pipeline
mode dropdown — they currently show a placeholder message. Phase 2 replaces those
stubs with real implementations, one pipeline at a time.

---

## Architecture Overview (Phase 2 additions)

```
User Query (Streamlit)
        |
        v
  Pipeline Mode selector
        |
   +---------+-----------+-----------+
   |         |           |           |
  CAG    Dense RAG   Hybrid RAG  Agentic RAG
(done)      |           |           |
            v           v           v
       Vector Store (FAISS)         |
       bge-m3 embeddings            |
       BM25 sparse index            |
            |           |           |
            |    alpha-weighted     |
            |    score + filters    |
            |                  LangGraph
            |                  Issue Agent
            |                  Statute Agent
            |                  Case Law Agent
            |                  Conflict Agent
            |           |           |
            +-----------+-----------+
                        |
                 Document Generator
                 (already wired for CAG;
                  extend to accept Chunks)
                        |
                   Exporter + Run Logger
                        |
                   Streamlit Preview
```

---

## CAG Pipeline — Two-Pass Generation

### Why single-pass generation fails

The original CAG pipeline made one LLM call with a prompt that asked the model to simultaneously understand the fact pattern, recall the relevant statute sections from the injected cache, structure a multi-section legal document, and insert correctly formatted citations — all in a single response. For a small model like LLaMA 3 8B or Groq's `llama-3.1-8b-instant`, this is too many competing objectives. The result was:

- Bare slot names (`[PARTIES_CLAUSE]`, `[SCHEDULE]`) left unfilled in the output
- Operative clauses duplicated or out of order
- Citations hallucinated or attached to the wrong clause
- Generic boilerplate instead of fact-specific content

### How two-pass generation works

The CAG engine now splits generation into two distinct phases when `template_slots` are provided (which is always the case when called from the frontend):

**Pass 1 — Per-slot focused generation**

For each template slot (e.g. `PARTIES_CLAUSE`, `RECITALS`, `OPERATIVE_CLAUSE_1`, `SCHEDULE`, `ATTESTATION`), the engine makes one dedicated LLM call via `_build_slot_prompt()`. Each prompt:

- Asks for 2–6 sentences of real legal text for exactly one named section
- Provides the full fact pattern and the cache context
- Requests a single inline citation (or `[UNGROUNDED]` if none applies)
- Explicitly forbids outputting the slot name as a placeholder

Because the task is narrow — "write the PARTIES section" rather than "write the entire deed" — the model stays focused and produces grounded, specific content for that section.

**Pass 2 — Deterministic assembly**

`_assemble_from_slots()` stitches the per-slot outputs into a final document body. It applies clean section headings (`PARTIES`, `RECITALS`, `OPERATIVE CLAUSE 1`, `SCHEDULE OF PROPERTY`, `ATTESTATION`) in the order defined by the template, with no LLM involvement. The assembly is deterministic and cannot hallucinate structure.

### Code path

```
generate_draft(fact_pattern, cache, doc_type, template_slots=[...])
    │
    ├── Pass 1: for each slot_name in template_slots:
    │       _build_slot_prompt(slot_name, fact_pattern, cache_context, doc_type)
    │       → _call_groq() or _call_ollama()
    │       → slot_contents[slot_name] = response
    │
    └── Pass 2:
            _assemble_from_slots(slot_contents, template_slots, doc_type)
            → clean headings + slot content joined in template order
            → returned as DraftResult.content
```

### Rate limit handling (Groq free tier)

Groq's free tier allows roughly 30 requests per minute for `llama-3.1-8b-instant`. Two-pass generation with 7 slots makes 7 sequential requests, which can trigger HTTP 429 responses on slots 2–7 if requests arrive too quickly.

The engine handles this at two levels:

**1. Exponential backoff in `_call_groq()`**

On a 429 response, the engine reads the `Retry-After` header from Groq's response (if present) and waits that many seconds before retrying. If no header is present, it falls back to exponential backoff: 2s → 4s → 8s. Up to 3 retries are attempted before returning `[GROQ_UNAVAILABLE — MANUAL REVIEW REQUIRED]`.

**2. Inter-slot delay in the two-pass loop**

A 2-second pause is inserted between each slot call (`time.sleep(2)`) when using a Groq backend. With 7 slots this adds ~12s to total latency but keeps the request rate well under the 30 req/min limit.

Updated latency table with rate-limit handling:

| Backend | Single-pass | Two-pass (7 slots, with 2s gaps) | Notes |
|---|---|---|---|
| Groq llama3-8b | ~3s | ~30s | Includes 12s inter-slot delay + backoff headroom |
| qwen2.5:1.5b | ~10 min | ~70 min | No rate limit; delay not applied |
| qwen2.5:7b | ~30 min | ~3.5 hrs (CPU) / ~14 min (GPU) | No rate limit; delay not applied |

If you still hit 429s (e.g. on a heavily shared API key), the backoff will extend individual slot calls by up to 14s (2+4+8). The document will still complete — no slots will be silently dropped.

### Fallback behaviour

If `template_slots` is `None` (e.g. when calling `generate_draft()` directly from a script without a template), the engine falls back to single-pass generation using `_build_generation_prompt()`. This preserves backward compatibility for automated test scripts and batch generation pipelines.

### Section-boundary-aware cache truncation

Alongside two-pass generation, `_build_cache_context()` was updated to truncate cache entries at legal section boundaries rather than raw character offsets. The truncation logic finds the last `Section N` header or blank-line-before-capital before the character limit and cuts there, ensuring the LLM never receives a half-sentence mid-section. Each cache manifest also now specifies a `section_hint` (e.g. `"122-129"` for Gift Deed TPA sections) so only the relevant statute sections are loaded into the context window rather than the entire act.

---

## CAG Evaluation Framework

### Why BLEU/ROUGE are insufficient for legal documents

The existing `scripts/evaluate_documents.py` computes BLEU and ROUGE against static reference templates. For legal documents this has two fundamental problems:

1. The reference is a generic template, not a fact-specific gold standard. A correctly drafted sale deed for a Pune property will score low BLEU against a Solapur mortgage deed reference — not because it is bad, but because it is a different document with different parties, amounts, and survey numbers.
2. Neither metric captures what actually matters for legal quality: are the cited statutes traceable to the loaded cache? Are the intake fields present verbatim? Is the document structurally complete?

### Three-tier evaluation framework

The evaluation framework is implemented in `src/evaluation/evaluator.py` and runs automatically on every CAG generation. It is structured in three tiers ordered by implementation effort vs research value.

---

#### Tier 1 — CAG-specific metrics (no external dependencies)

These are computable directly from the existing pipeline outputs and run on every document generation. They are the most important metrics for evaluating the CAG pipeline specifically.

| Metric | Definition | Source |
|---|---|---|
| **Cache Hit Rate** | `grounded_citations / (grounded + ungrounded)` | `GeneratedDocument.citations` vs `ungrounded_clauses` |
| **Slot Fill Rate** | `filled_slots / total_template_slots` | Checks each slot heading has ≥ 50 tokens of real content |
| **Fact Fidelity Score** | `intake_fields_found_in_output / total_intake_fields` | String-match of each intake value against the document body |
| **Latency (seconds)** | Wall-clock time from `generate_draft()` call to `DraftResult` | `time.time()` around the generation call |

**Cache Hit Rate** is the defining metric for CAG. A high cache hit rate (> 0.8) means the LLM is grounding its claims in the loaded statute cache rather than hallucinating. A low rate means the cache manifest needs better section hints or the LLM is citing acts not in the cache.

**Fact Fidelity Score** catches the most common failure mode: the LLM ignoring the intake form and generating generic boilerplate. If party names, survey numbers, or consideration amounts from the intake are absent from the output, the score drops.

---

#### Tier 2 — Structural/legal quality metrics (regex-based)

These run without any LLM and check the structural and legal correctness of the output.

| Metric | Definition |
|---|---|
| **Section Completeness** | Fraction of expected headings (PARTIES, RECITALS, OPERATIVE CLAUSE, SCHEDULE, ATTESTATION) present in the output |
| **Citation Format Compliance** | Fraction of citations matching `[Act Name, Year] Section X(Y), [Court], [Year]` exactly |
| **Jurisdictional Accuracy** | Fraction of cited acts belonging to the known Maharashtra/Central acts list |

**Jurisdictional Accuracy** is particularly important for the research paper — it measures whether the pipeline is staying within Maharashtra jurisdiction rather than citing irrelevant central or foreign statutes.

---

#### Tier 3 — RAGAS (LLM-judge, requires `ragas` + `datasets`)

RAGAS is designed for RAG pipelines and maps cleanly onto CAG. It requires an LLM judge (uses OpenAI by default, configurable to use Groq) and is intended for the research paper comparison rather than per-document evaluation.

| Metric | Definition | CAG mapping |
|---|---|---|
| **Faithfulness** | Are the claims in the output supported by the context? | Cache documents = context; document body = answer |
| **Answer Relevance** | Does the output address the question? | Fact pattern = question; document body = answer |
| **Context Precision** | What fraction of the loaded cache was actually used? | Measures cache efficiency |
| **Context Recall** | Did the cache contain what was needed? | Measures cache completeness |

**Faithfulness** is the most defensible metric for the research paper — it directly measures the core claim of each pipeline (CAG: fixed cache grounds the output; Dense/Hybrid RAG: dynamic retrieval grounds the output).

Install RAGAS:
```bash
pip install ragas datasets
```

Run RAGAS evaluation on a document:
```python
from src.evaluation.evaluator import evaluate_ragas
ragas_scores = evaluate_ragas(generated_doc, cache, fact_pattern)
# Returns: {"faithfulness": 0.87, "answer_relevance": 0.91, ...}
```

---

#### Answer Degradation vs Fresh Retrieval

This is a CAG-specific comparative metric that answers: *does the fixed cache produce worse output than a fresh retrieval would?*

```python
from src.evaluation.evaluator import compute_answer_degradation
degradation = compute_answer_degradation(cag_result, dense_rag_result)
# Returns: {"cache_hit_rate_delta": +0.12, "slot_fill_rate_delta": -0.03, ...}
```

A **positive delta** means CAG performed worse than Dense RAG on that metric. A **negative delta** means CAG performed better. For the research paper, if CAG faithfulness ≥ Dense RAG faithfulness, the fixed cache is not degrading quality relative to dynamic retrieval — which is the central claim of the CAG approach.

---

### Metric table for the research paper

| Metric | CAG | Dense RAG | Hybrid RAG | Agentic RAG |
|---|---|---|---|---|
| Cache Hit Rate | ✓ | N/A | N/A | N/A |
| Slot Fill Rate | ✓ | ✓ | ✓ | ✓ |
| Fact Fidelity Score | ✓ | ✓ | ✓ | ✓ |
| Latency (s) | ✓ | ✓ | ✓ | ✓ |
| Section Completeness | ✓ | ✓ | ✓ | ✓ |
| Citation Format Compliance | ✓ | ✓ | ✓ | ✓ |
| Jurisdictional Accuracy | ✓ | ✓ | ✓ | ✓ |
| RAGAS Faithfulness | ✓ | ✓ | ✓ | ✓ |
| RAGAS Answer Relevance | ✓ | ✓ | ✓ | ✓ |
| RAGAS Context Precision | ✓ | ✓ | ✓ | ✓ |
| RAGAS Context Recall | ✓ | ✓ | ✓ | ✓ |
| Answer Degradation vs Fresh | ✓ | baseline | — | — |

---

### Integration with the pipeline

Tier 1 and Tier 2 metrics are computed automatically on every CAG generation in the Streamlit frontend. The results appear in a collapsible "Document Quality Metrics" panel below the draft preview, showing all seven metrics as Streamlit metric cards.

The evaluation is wired into `_run_generation_phase()` in `src/frontend/app.py`:

```python
# After generate_document() returns:
from src.evaluation.evaluator import evaluate_cag_document
eval_result = evaluate_cag_document(
    doc=generated,
    fact_pattern=fact_pattern,
    template_slots=_slot_names,
    latency_seconds=_gen_latency,
)
st.session_state.eval_result = eval_result.summary()
```

The `summary()` method returns a flat dict suitable for JSON serialisation, which is also written to the run log for offline analysis.

---

### Running the full evaluation suite (Phase 2)

Once Phase 2 RAG pipelines are implemented, run the cross-pipeline evaluation:

```bash
python scripts/run_evaluation.py
# Output: output/evaluation_report.json + output/evaluation_report.csv
```

This runs all 4 pipelines on the 20-query eval set in `config/eval_queries.json` and produces the comparison table for the research paper. RAGAS metrics require a valid `OPENAI_API_KEY` or a configured Groq judge.

---

### Cache manifest naming convention (citation matching)

A critical dependency for Cache Hit Rate is that cache manifest entry names match exactly what the LLM cites. The LLM cites acts in the format `[Registration Act, 1908]` — so manifest names must use the same `Act Name, Year` format with a comma before the year.

All 7 manifests have been updated to use canonical names:

| Document Type | Primary Act Names in Manifest |
|---|---|
| Sale Deed | `Transfer of Property Act, 1882` · `Registration Act, 1908` |
| Gift Deed | `Transfer of Property Act, 1882` · `Registration Act, 1908` |
| Mortgage Deed | `Transfer of Property Act, 1882` · `Registration Act, 1908` |
| Leave & License | `Maharashtra Rent Control Act, 1999` · `Registration Act, 1908` · `Transfer of Property Act, 1882` |
| Power of Attorney | `Registration Act, 1908` · `Transfer of Property Act, 1882` |
| Conveyance Deed | `Transfer of Property Act, 1882` · `Registration Act, 1908` |
| Affidavit | `Registration Act, 1908` · `Indian Evidence Act, 1872` |

If the LLM cites an act not in this list (e.g. `Maharashtra Land Revenue Code, 1966`), the citation verifier will mark it as ungrounded and the Cache Hit Rate will drop. Add the act to the relevant manifest to fix this.

---

Install these before starting:

```bash
pip install sentence-transformers faiss-cpu rank-bm25 langgraph ragas
```

For GPU machines replace `faiss-cpu` with `faiss-gpu`.

Environment variables to add to `.env`:

```
VECTOR_STORE_TYPE=faiss
FAISS_INDEX_PATH=./data/faiss_index
EMBEDDING_MODEL=bge-m3
EMBEDDING_BATCH_SIZE=32
HYBRID_ALPHA=0.6
EVAL_QUERY_SET_PATH=./config/eval_queries.json
```

---


## Step-by-Step Implementation

Each step is self-contained. Complete one step, verify it works in Streamlit, then move to the next.

---

### STEP 1 — Corpus Ingestion Infrastructure

**Goal:** Chunk all 600+ legal PDFs, embed them, and store in a FAISS index.
This is the foundation that Dense RAG, Hybrid RAG, and Agentic RAG all depend on.

#### 1.1 Semantic Legal Chunker (`src/rag/chunker.py`)

Split documents at Section/Sub-section/Clause boundaries, not fixed character counts.

```python
@dataclass
class ChunkMetadata:
    court_type: str       # "High Court" | "District Court" | "Legislature"
    year: int
    statute_reference: str
    language: str         # "English" | "Marathi" | "Bilingual"
    district: str         # "Mumbai" | "Pune" | "State-wide" | "Central"
    document_type: str    # "Act" | "Judgment" | "Template" | "Deed"
    source_path: str
    section_ref: str      # e.g. "Section 32(1)"

@dataclass
class Chunk:
    chunk_id: str         # UUID
    text: str
    tokens: int
    metadata: ChunkMetadata
    embedding: list[float] | None

def chunk_document(pdf_path: str) -> list[Chunk]:
    ...
```

Rules:
- Max 512 tokens per chunk, 50-token overlap
- If PDF has no text layer, route through `src/ocr/pipeline.py` first
- Infer `language` from Devanagari character presence (U+0900-U+097F)
- Infer `year` from filename or first-page header regex
- Infer `court_type` from directory path (`BHC_Legal_Documents` = High Court, etc.)

Dataset directories to ingest:
```
Maharashtra Legal Document Dataset/BHC_Legal_Documents/01_Maharashtra_Acts/
Maharashtra Legal Document Dataset/BHC_Legal_Documents/02_Recent_Statutes/
Maharashtra Legal Document Dataset/Maharashtra Legal Corpus/
Maharashtra Legal Document Dataset/Mortage and land deed/
```

#### 1.2 Embedder (`src/rag/embedder.py`)

```python
def embed_chunks(chunks: list[Chunk], model: str = "bge-m3") -> list[Chunk]:
    # Uses sentence-transformers
    # bge-m3 for English chunks
    # LaBSE for Marathi/Bilingual chunks (when EMBEDDING_MODEL=labse)
    ...
```

Process in batches of `EMBEDDING_BATCH_SIZE` (default 32) to avoid OOM.

#### 1.3 FAISS Vector Store (`src/rag/vector_store.py`)

```python
def build_index(chunks: list[Chunk], index_path: str) -> None:
    # Saves FAISS flat L2 index + chunk metadata JSON to index_path
    ...

def load_index(index_path: str) -> tuple[faiss.Index, list[Chunk]]:
    # Loads on startup; no re-ingestion needed
    ...

def search(query_embedding: list[float], top_k: int = 10) -> list[Chunk]:
    ...
```

Persist both the FAISS binary index and a `chunks_metadata.json` sidecar file.

#### 1.4 BM25 Sparse Index (`src/rag/bm25_index.py`)

```python
def build_bm25(chunks: list[Chunk]) -> BM25Okapi:
    # rank-bm25 library
    ...

def search_bm25(query: str, chunks: list[Chunk], top_k: int = 10) -> list[tuple[Chunk, float]]:
    ...
```

#### 1.5 Ingestion Runner (`src/rag/ingestor.py`)

```python
@dataclass
class IngestionReport:
    total_documents: int
    chunks_created: int
    chunks_embedded: int
    failed_documents: list[str]
    round_trip_pass_rate: float

def ingest_corpus(dataset_dirs: list[str]) -> IngestionReport:
    # Orchestrates: chunk -> embed -> store in FAISS + BM25
    # Runs round-trip validation on 10% random sample
    # Halts if round-trip pass rate < 95%
    ...
```

Run ingestion once:
```bash
python scripts/ingest_corpus.py
```

Create `scripts/ingest_corpus.py` that calls `ingest_corpus()` and prints the report.

**Verification:** After ingestion, `data/faiss_index/` should contain the index files.
Run a quick sanity search:
```bash
python -c "from src.rag.vector_store import load_index, search; ..."
```

---

### STEP 2 — LegalDocumentAST Parser (Round-Trip Validation)

**Goal:** Structured parse/print for legal documents — required for ingestion validation.

File: `src/rag/ast_parser.py`

```python
@dataclass
class Clause:
    label: str
    text: str

@dataclass
class SubSection:
    number: str
    text: str
    clauses: list[Clause]

@dataclass
class Section:
    number: str
    title: str
    sub_sections: list[SubSection]
    clauses: list[Clause]

@dataclass
class LegalDocumentAST:
    title: str
    act_number: str
    year: int
    sections: list[Section]

class LegalDocumentParser:
    def parse(self, text: str) -> LegalDocumentAST: ...

class LegalDocumentPrinter:
    def print(self, ast: LegalDocumentAST) -> str: ...
```

Round-trip property: `parse(print(parse(doc))) == parse(doc)` must hold for 95%+ of corpus.
The ingestion runner calls this on a 10% random sample and halts if it fails.

---

### STEP 3 — Dense RAG Pipeline

**Goal:** Wire Dense RAG into the Streamlit frontend as a working pipeline mode.

#### 3.1 Dense Retriever (`src/rag/dense_retriever.py`)

```python
def retrieve_dense(
    query: str,
    top_k: int = 10,
    language_filter: str | None = None,   # "English" | "Marathi" | "Bilingual"
) -> list[Chunk]:
    # 1. Embed query with same model used for corpus
    # 2. FAISS cosine search
    # 3. Apply language_filter if set
    # 4. Log chunk IDs, scores, source metadata
    ...
```

#### 3.2 Wire Dense RAG into Document Generator

`src/generation/document_generator.py` currently accepts `LegalCache | list[Chunk]`.
The `_ChunkListWrapper` and `_ChunkAsEntry` adapters are already in place.
No changes needed to `document_generator.py` — it already handles `list[Chunk]`.

#### 3.3 Wire Dense RAG into `src/frontend/app.py`

In `_run_generation_phase()`, replace the Dense RAG stub:

```python
# BEFORE (stub):
elif pipeline_mode == "Dense RAG":
    llm_output = "[Dense RAG pipeline is available in Phase 2.]..."
    cache_or_chunks = None

# AFTER:
elif pipeline_mode == "Dense RAG":
    from src.rag.dense_retriever import retrieve_dense
    from src.cag.engine import generate_draft_from_chunks
    chunks = retrieve_dense(fact_pattern_str, top_k=10)
    draft_result = generate_draft_from_chunks(fact_pattern, chunks, doc_type, llm_backend)
    llm_output = draft_result.content
    cache_or_chunks = chunks
    cache_version = "dense-rag-v1"
```

Add `generate_draft_from_chunks()` to `src/cag/engine.py` — same as `generate_draft()`
but takes `list[Chunk]` instead of `LegalCache`. Builds context from chunk texts.

**Streamlit verification:** Select "Dense RAG" in the sidebar, fill the questionnaire,
click generate. Should produce a real document (not the stub message).

---

### STEP 4 — Hybrid RAG Pipeline

**Goal:** Add weighted dense+BM25 retrieval with metadata filters.

#### 4.1 Hybrid Retriever (`src/rag/hybrid_retriever.py`)

```python
def retrieve_hybrid(
    query: str,
    metadata_filters: dict,   # e.g. {"court_type": "High Court", "year_min": 2015}
    alpha: float = 0.6,       # from HYBRID_ALPHA env var
    top_k: int = 10,
) -> list[Chunk]:
    # 1. Dense search (top 50 candidates)
    # 2. BM25 search (top 50 candidates)
    # 3. Normalize scores to [0,1]
    # 4. Weighted score = alpha * dense + (1-alpha) * bm25
    # 5. Apply metadata_filters
    # 6. If no chunks pass filters: relax district -> retry; relax year -> retry; log each step
    # 7. Return top_k by weighted score
    ...
```

Filter relaxation order (as per design):
1. Apply all filters
2. If empty: drop `district` filter, retry, log "relaxed district filter"
3. If still empty: drop `year` filter, retry, log "relaxed year filter"
4. If still empty: return top_k with no filters, log "all filters relaxed"

#### 4.2 Wire Hybrid RAG into `app.py`

Same pattern as Dense RAG. Replace the Hybrid RAG stub in `_run_generation_phase()`:

```python
elif pipeline_mode == "Hybrid RAG":
    from src.rag.hybrid_retriever import retrieve_hybrid
    chunks = retrieve_hybrid(fact_pattern_str, metadata_filters={}, alpha=0.6)
    draft_result = generate_draft_from_chunks(fact_pattern, chunks, doc_type, llm_backend)
    llm_output = draft_result.content
    cache_or_chunks = chunks
    cache_version = "hybrid-rag-v1"
```

**Streamlit verification:** Select "Hybrid RAG", generate a document. Check that the
citation index references different sources than Dense RAG for the same query.

---

### STEP 5 — Agentic RAG Pipeline (LangGraph)

**Goal:** Multi-agent pipeline with Issue, Statute, Case Law, and Conflict agents.
This is the most complex step — implement last.

#### 5.1 Agent State and Data Classes (`src/agents/state.py`)

```python
@dataclass
class TraceEntry:
    agent: str
    timestamp: str
    input_summary: str
    output_summary: str
    retrieval_scores: list[float]
    duration_ms: int

@dataclass
class ConflictNote:
    issue: str
    statute_ref: str
    case_law_ref: str
    resolution: str

@dataclass
class AgentState:
    fact_pattern: str
    legal_issues: list[str]
    statute_chunks: list[Chunk]
    case_law_chunks: list[Chunk]
    conflicts: list[ConflictNote]
    draft: str
    trace: list[TraceEntry]
    errors: list[str]
```

#### 5.2 Issue Agent (`src/agents/issue_agent.py`)

Parses the fact pattern and produces a list of legal points of contention.
Uses the LLM (Groq) with a focused prompt:

```
Given this fact pattern for a {doc_type}, list the key legal issues
that must be addressed in the document. Return as a numbered list.
```

Timeout: 60 seconds. On timeout, use the full fact pattern as a single issue.

#### 5.3 Statute Agent (`src/agents/statute_agent.py`)

For each legal issue, retrieves top-5 Act chunks using Hybrid RAG with filter
`document_type = "Act"`. Runs in parallel with Case Law Agent.

#### 5.4 Case Law Agent (`src/agents/case_law_agent.py`)

For each legal issue, retrieves top-5 judgment chunks with filter `year >= 2015`.
Runs in parallel with Statute Agent.

#### 5.5 Conflict Agent (`src/agents/conflict_agent.py`)

Compares statute chunks vs case law chunks per issue. Uses LLM to identify
contradictions. Produces `ConflictNote` per contradiction. If no conflicts,
returns empty list and logs "no conflicts detected".

#### 5.6 LangGraph Orchestrator (`src/agents/orchestrator.py`)

```python
from langgraph.graph import StateGraph

def build_agentic_graph() -> StateGraph:
    graph = StateGraph(AgentState)
    graph.add_node("issue_agent", run_issue_agent)
    graph.add_node("statute_agent", run_statute_agent)
    graph.add_node("case_law_agent", run_case_law_agent)
    graph.add_node("conflict_agent", run_conflict_agent)

    graph.set_entry_point("issue_agent")
    graph.add_edge("issue_agent", "statute_agent")
    graph.add_edge("issue_agent", "case_law_agent")
    graph.add_edge("statute_agent", "conflict_agent")
    graph.add_edge("case_law_agent", "conflict_agent")
    graph.add_edge("conflict_agent", END)
    return graph.compile()

def run_agentic_pipeline(fact_pattern: str, doc_type: str) -> AgentState:
    graph = build_agentic_graph()
    initial_state = AgentState(fact_pattern=fact_pattern, ...)
    return graph.invoke(initial_state)
```

Each agent node wraps its logic in a 60-second timeout using `concurrent.futures`.
On timeout: insert `[AGENT_TIMEOUT — PARTIAL RESULTS]` and continue.

#### 5.7 Wire Agentic RAG into Document Generator

`document_generator.py` needs one addition: when `ConflictNote` objects are present,
append them as footnotes at the end of the body before the citation index.

#### 5.8 Wire Agentic RAG into `app.py`

Replace the Agentic RAG stub in `_run_generation_phase()`:

```python
elif pipeline_mode == "Agentic RAG":
    from src.agents.orchestrator import run_agentic_pipeline
    agent_state = run_agentic_pipeline(fact_pattern_str, doc_type)
    all_chunks = agent_state.statute_chunks + agent_state.case_law_chunks
    # Build LLM output from agent state (statute + case law context)
    draft_result = generate_draft_from_chunks(
        fact_pattern, all_chunks, doc_type, llm_backend,
        conflict_notes=agent_state.conflicts
    )
    llm_output = draft_result.content
    cache_or_chunks = all_chunks
    cache_version = "agentic-rag-v1"
    # Store trace for the reasoning panel
    st.session_state.trace_entries = [
        {"agent": t.agent, "timestamp": t.timestamp,
         "input_summary": t.input_summary, "output_summary": t.output_summary,
         "retrieval_scores": t.retrieval_scores, "duration_ms": t.duration_ms}
        for t in agent_state.trace
    ]
```

The reasoning trace panel in `app.py` is already wired to `st.session_state.trace_entries`
and renders automatically when Agentic RAG is selected.

**Streamlit verification:** Select "Agentic RAG", generate a document. The reasoning
trace panel should appear below the draft showing each agent's invocation.

---

### STEP 6 — Evaluator and Benchmarking

**Goal:** Compute the full metric suite across all 4 pipelines on a 20-query eval set.

The Tier 1 and Tier 2 evaluator (`src/evaluation/evaluator.py`) is already implemented and runs automatically on every CAG generation. Step 6 adds the eval query set, the cross-pipeline runner, and RAGAS integration.

#### 6.1 Evaluation Query Set (`config/eval_queries.json`)

Create 20 synthetic legal drafting queries covering all 7 document types:

```json
[
  {
    "query_id": "q001",
    "document_type": "sale_deed",
    "fact_pattern": {
      "intake": {
        "vendor_name": "Suresh Patil",
        "purchaser_name": "Anita Mehta",
        "survey_number": "45/2A",
        "area": "500 sq metres",
        "consideration": "Rs.25,00,000",
        "district": "Pune"
      }
    }
  }
]
```

#### 6.2 Cross-pipeline evaluation runner (`scripts/run_evaluation.py`)

```python
# For each query × pipeline:
#   1. Run generate_draft() / retrieve_dense() / retrieve_hybrid() / run_agentic_pipeline()
#   2. Call evaluate_cag_document() for Tier 1+2
#   3. Call evaluate_ragas() for Tier 3
#   4. Call compute_answer_degradation() for CAG vs Dense RAG
#   5. Write to output/evaluation_report.json + .csv
```

```bash
python scripts/run_evaluation.py
# Output: output/evaluation_report.json + output/evaluation_report.csv
```

#### 6.3 RAGAS configuration

RAGAS uses an LLM judge. Configure it to use Groq instead of OpenAI:

```python
import os
from ragas.llms import LangchainLLMWrapper
from langchain_groq import ChatGroq

os.environ["GROQ_API_KEY"] = "..."
groq_llm = LangchainLLMWrapper(ChatGroq(model="llama-3.1-8b-instant"))

# Pass to ragas evaluate():
result = ragas_evaluate(dataset, metrics=[faithfulness, ...], llm=groq_llm)
```

Add to `.env`:
```
RAGAS_LLM_BACKEND=groq   # "groq" | "openai"
```

---

### STEP 7 — Multilingual Enhancements

**Goal:** LaBSE embeddings for Marathi chunks, Devanagari query detection.

#### 7.1 LaBSE embedding path in `embedder.py`

When `EMBEDDING_MODEL=labse`, use `sentence-transformers/LaBSE` for chunks
where `metadata.language` is "Marathi" or "Bilingual".

#### 7.2 Marathi query detection in `dense_retriever.py`

```python
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")

def _is_marathi_query(query: str) -> bool:
    return bool(_DEVANAGARI_RE.search(query))
```

When a Marathi query is detected, retrieve from both Marathi and English partitions
and merge results before returning top_k.

---

## Streamlit Integration Summary

The frontend already has all four pipeline modes in the dropdown. The only file
that changes for each step is `src/frontend/app.py` — specifically the
`_run_generation_phase()` function. Each pipeline mode replaces its stub block:

| Pipeline | Stub location in app.py | Replaced by |
|---|---|---|
| Dense RAG | `elif pipeline_mode == "Dense RAG":` | `retrieve_dense()` + `generate_draft_from_chunks()` |
| Hybrid RAG | `elif pipeline_mode == "Hybrid RAG":` | `retrieve_hybrid()` + `generate_draft_from_chunks()` |
| Agentic RAG | `elif pipeline_mode == "Agentic RAG":` | `run_agentic_pipeline()` + trace wiring |

The reasoning trace panel, citation panel, download buttons, and run logger all
work unchanged for all pipeline modes — they operate on the same `GeneratedDocument`
output regardless of which pipeline produced it.

---

## Implementation Order (Recommended)

```
Step 1: Corpus Ingestion  (chunker + embedder + FAISS + BM25 + ingestor)
Step 2: AST Parser        (round-trip validation for ingestion)
Step 3: Dense RAG         (retriever + app.py wiring)  <-- first working RAG mode
Step 4: Hybrid RAG        (weighted retriever + app.py wiring)
Step 5: Agentic RAG       (LangGraph agents + orchestrator + app.py wiring)
Step 6: Evaluator         (metrics + eval script)
Step 7: Multilingual      (LaBSE + Devanagari detection)
```

Steps 3, 4, and 5 are independent of each other after Step 1 completes.
You can implement and test each RAG mode in Streamlit before moving to the next.

---

## New Files to Create

```
src/rag/
  chunker.py          Step 1.1
  embedder.py         Step 1.2
  vector_store.py     Step 1.3
  bm25_index.py       Step 1.4
  ingestor.py         Step 1.5
  ast_parser.py       Step 2
  dense_retriever.py  Step 3.1
  hybrid_retriever.py Step 4.1

src/agents/
  state.py            Step 5.1
  issue_agent.py      Step 5.2
  statute_agent.py    Step 5.3
  case_law_agent.py   Step 5.4
  conflict_agent.py   Step 5.5
  orchestrator.py     Step 5.6

src/evaluation/
  evaluator.py        DONE — Tier 1+2 implemented; Tier 3 (RAGAS) ready to call

scripts/
  ingest_corpus.py    Step 1.5
  run_evaluation.py   Step 6.3 (cross-pipeline runner; Tier 1+2 already wired)

config/
  eval_queries.json   Step 6.1

data/
  faiss_index/        Created by ingestion (gitignored)
```

---

## Files Modified (not created)

| File | Change |
|---|---|
| `src/cag/engine.py` | Add `generate_draft_from_chunks(fact_pattern, chunks, doc_type, llm_backend, conflict_notes=None)` |
| `src/generation/document_generator.py` | Accept `conflict_notes` param; append as footnotes before citation index |
| `src/generation/citation_verifier.py` | Add `verify_against_chunks(text, chunks)` path alongside existing `verify_citations` |
| `src/frontend/app.py` | Replace Dense/Hybrid/Agentic stubs in `_run_generation_phase()` |
| `requirements.txt` | Add `sentence-transformers`, `faiss-cpu`, `rank-bm25`, `langgraph`, `ragas` |
| `.env.example` | Add `VECTOR_STORE_TYPE`, `FAISS_INDEX_PATH`, `EMBEDDING_MODEL`, `EMBEDDING_BATCH_SIZE`, `HYBRID_ALPHA`, `EVAL_QUERY_SET_PATH` |

---

## Key Design Constraints

- All LLM inference stays local (Groq or Ollama) — no new external API calls for document content
- FAISS is used instead of Qdrant to avoid running a separate server (simpler local deployment)
- Each RAG mode must produce a `GeneratedDocument` object identical in structure to CAG output
- The run logger, exporter, and citation panel work unchanged for all pipeline modes
- Ingestion is a one-time offline step; the FAISS index is loaded on startup from disk
- Groq remains the default LLM backend for all pipeline modes (fast, no local GPU needed)
