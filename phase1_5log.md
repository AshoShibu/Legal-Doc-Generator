# Phase 1.5 Change Log

This file tracks every file added or modified during Phase 1.5 development.
It is intended to make cloud deployment straightforward — a deployer can read
this log top-to-bottom and know exactly what changed, why, and what environment
variables or dependencies are required.

---

## Format

Each entry follows this structure:

```
### [Task N] <short description>  — <date>
**Files changed:** list of paths
**What changed:** bullet summary
**Deploy notes:** anything the deployer must do (env vars, pip install, etc.)
```

---

## Entries

---

### [Task 1] Environment setup and feature flag infrastructure — 2026-04-10

**Files changed:**
- `.env.example` — added Phase 1.5 environment variables
- `config/settings.py` — added Phase 1.5 settings fields, `log_feature_flags()`, and convenience properties
- `requirements.txt` — added `psutil==5.9.8` and `tqdm==4.66.4`
- `phase1_5log.md` — created this file

**What changed:**

1. **`.env.example`** — appended a clearly delimited Phase 1.5 section with:
   - Feature flags: `ENABLE_PHASE_1_5`, `ENABLE_CITATION_FORMATTING`, `ENABLE_UNDERLINE_FORMATTING`, `ENABLE_MODEL_COMPARISON`, `ENABLE_CHAT_BACKEND`
   - Model comparison config: `MODEL_COMPARISON_OUTPUT_DIR`, `INCLUDE_LARGE_MODELS`
   - Chat backend config: `CHAT_BACKEND_PORT`, `CHAT_SESSION_STORE`, `REDIS_URL`, `CHAT_SESSION_TTL_MINUTES`, `CORS_ORIGINS`
   - Rate limiting: `RATE_LIMIT_PER_MINUTE`, `RATE_LIMIT_PER_HOUR`
   - Security: `ADMIN_API_KEY`, `ENABLE_HTTPS`

2. **`config/settings.py`** — extended the `Settings` dataclass with:
   - All Phase 1.5 env vars as typed fields with safe defaults
   - `log_feature_flags()` method — call at app startup to log all flag states
   - Convenience properties: `citation_formatting_enabled`, `underline_formatting_enabled`, `model_comparison_enabled`, `chat_backend_enabled` — each respects the master `ENABLE_PHASE_1_5` switch
   - Added `import logging` (no new dependency)

3. **`requirements.txt`** — added:
   - `psutil==5.9.8` — used by `Model_Registry` to detect available system RAM and auto-enable/disable 70B+ optional models
   - `tqdm==4.66.4` — used by model comparison CLI for progress bars

**Deploy notes:**
- Run `pip install -r requirements.txt` to pick up `psutil` and `tqdm`
- Copy `.env.example` additions into your `.env` (or cloud secret manager)
- All new flags default to `true` / safe values — no action needed to keep Phase 1 behaviour unchanged
- To fully disable Phase 1.5: set `ENABLE_PHASE_1_5=false` in your environment
- Call `settings.log_feature_flags()` early in your app entrypoint (e.g. `app.py`) to confirm flag states in logs

---

### [Task 2] Implement `Citation_Formatter` — 2026-04-10

**Files changed:**
- `src/generation/citation_formatter.py` — **new file**

**What changed:**

New module implementing the end-of-document citation index system. The LLM is
instructed (via updated prompts in Task 4) to emit `[CITE:Act Name, Year, Section X(Y)]`
markers inline. This module collects, deduplicates, and formats them.

Public API:

| Function | Purpose |
|---|---|
| `normalize_citation_key(act, year, section)` | Lowercase pipe-delimited key for deduplication |
| `collect_citations(text)` | Parse `[CITE:...]` markers; return text unchanged + `CitationMarker` list |
| `assign_reference_numbers(citations)` | Sequential 1…N by first appearance; duplicates reuse same number |
| `replace_markers(text, citation_map)` | Swap `[CITE:...]` with `[N]`; malformed → `[UNGROUNDED — MANUAL REVIEW REQUIRED]` |
| `build_citation_index(citations)` | Format `--- CITATION INDEX ---` block per Indian Legal Citation Standard |

Dataclasses added: `CitationMarker`, `CitationIndex`

Zero-citation case: appends `(No citations found in this document.)`

Malformed marker handling: logs a warning, substitutes `[UNGROUNDED — MANUAL REVIEW REQUIRED]`

**Deploy notes:**
- No new dependencies — stdlib only (`re`, `logging`, `dataclasses`)
- No environment variables required for this module itself
- Activated at runtime by `ENABLE_CITATION_FORMATTING=true` (see Task 4)

---

### [Task 3] Implement `Formatting_Engine` — 2026-04-10

**Files changed:**
- `src/generation/formatting_engine.py` — **new file**

**What changed:**

New module for detecting and applying underlining to party names in generated
legal documents, conforming to Maharashtra legal drafting conventions.

**Subtask 3.1 — Detection functions:**

| Function | Purpose |
|---|---|
| `detect_party_names(text, doc_type)` | Returns `(start, end, role)` tuples for all party name occurrences |
| `detect_operative_clauses(text)` | Returns `(start, end)` tuples for operative clause headings |
| `parse_underline_markers(text)` | Strips `<UNDERLINE>...</UNDERLINE>` tags; returns `(clean_text, ranges)` |

Party roles by document type:

| doc_type | Roles |
|---|---|
| `sale_deed` | Vendor, Purchaser, Vendee |
| `mortgage_deed` | Mortgagor, Mortgagee |
| `power_of_attorney` | Principal, Attorney, Agent |
| `leave_and_license` | Licensor, Licensee |
| `gift_deed` | Donor, Donee |
| `conveyance_deed` | Conveyor, Transferee |
| `affidavit` | Deponent |

All regex patterns compiled once at module load time. Matches inside `[CITE:...]`
markers and ALL-CAPS lines (headings/header) are excluded.

**Subtask 3.3 — Underlining application (parties only):**

| Function | Purpose |
|---|---|
| `apply_underline_docx(doc, ranges)` | Splits python-docx runs at range boundaries; sets `font.underline = True`; falls back to plain export on error |
| `apply_underline_pdf(pdf_content, ranges)` | Returns content unchanged with a warning — post-render byte-level underlining not supported by reportlab |
| `underline_parties_pdf_html(text, ranges)` | **Correct PDF path** — injects `<u>...</u>` tags into paragraph HTML before passing to reportlab `Paragraph()` |
| `get_underline_ranges(text, doc_type, parties_only=True)` | High-level helper: uses `<UNDERLINE>` markers if present, otherwise falls back to `detect_party_names` heuristic |

> **Note:** Operative clause underlining is intentionally excluded per project decision.
> Only party names are underlined.

**Deploy notes:**
- No new dependencies — stdlib only (`re`, `logging`, `dataclasses`)
- `python-docx` and `reportlab` are existing Phase 1 dependencies (already in `requirements.txt`)
- Activated at runtime by `ENABLE_UNDERLINE_FORMATTING=true` (see Task 4)

---

### [Task 4] Update system prompts and integrate formatters into pipeline — 2026-04-10

**Files changed:**
- `src/cag/engine.py` — updated `_build_slot_prompt()`
- `src/generation/document_generator.py` — updated `generate_document()`
- `src/generation/exporter.py` — updated `export_docx()` and `_build_pdf_story()`

**What changed:**

**`src/cag/engine.py` — `_build_slot_prompt()`:**
- `citation_instruction` now tells the LLM to output `[CITE:Act Name, Year, Section X(Y)]` markers (Phase 1.5 format) instead of the old inline citation format
- RULES block now includes: *"Wrap every party name … in `<UNDERLINE>...</UNDERLINE>` tags each time it appears"*
- Both instructions are injected into every slot prompt except `SCHEDULE` and `ATTESTATION`

**`src/generation/document_generator.py` — `generate_document()`:**
- Added `from config.settings import settings` import
- After `_map_llm_to_template()`, a new block runs `Citation_Formatter` when `settings.enable_citation_formatting` is `True`:
  1. `collect_citations(body)` — parse markers
  2. `assign_reference_numbers(citation_markers)` — build ref map
  3. `replace_markers(body, citation_map)` — swap to `[N]`
  4. `build_citation_index(citation_markers)` — format index
- The Phase 1.5 citation index overrides the Phase 1 index when available
- When flag is `False`: behaviour is identical to Phase 1 (no code path change)

**`src/generation/exporter.py` — `export_docx()`:**
- After `_add_body_with_bookmarks()`, a new block runs `Formatting_Engine` when `settings.enable_underline_formatting` is `True`:
  - Calls `get_underline_ranges(document.body, document.doc_type, parties_only=True)`
  - Calls `apply_underline_docx(doc, ranges)` if ranges found
  - Falls back to plain export (logs warning) on any exception

**`src/generation/exporter.py` — `_build_pdf_story()`:**
- Body paragraph loop replaced with a cursor-tracking version
- When `settings.enable_underline_formatting` is `True`:
  - Pre-computes `get_underline_ranges()` for the full body
  - For each paragraph, translates global ranges to paragraph-local offsets
  - Calls `underline_parties_pdf_html(para_text, local_ranges)` to inject `<u>` tags before passing to reportlab
  - Falls back to plain paragraph on any exception

**`app.py` — no changes required.** Integration is fully automatic through `generate_document()` and `export_document()`.

**Deploy notes:**
- No new dependencies for this task
- Feature flags control everything:
  - `ENABLE_CITATION_FORMATTING=true` → citation index uses Phase 1.5 `[N]` format
  - `ENABLE_CITATION_FORMATTING=false` → Phase 1 inline citation behaviour unchanged
  - `ENABLE_UNDERLINE_FORMATTING=true` → party names underlined in DOCX and PDF
  - `ENABLE_UNDERLINE_FORMATTING=false` → documents exported without underlining
- `ENABLE_PHASE_1_5=false` disables both regardless of individual flags

---

### [Task 5 — Checkpoint] Citations & Formatting validated + model-agnostic hardening — 2026-04-10

**Files changed:**
- `src/generation/formatting_engine.py` — heuristic detector hardened; `_TITLED_NAME_PATTERN` added; `get_underline_ranges` updated
- `src/generation/exporter.py` — PDF body loop fixed; `_build_pdf_story` citation anchor logic fixed
- `scripts/benchmark_citations_formatting.py` — **new file** (latency benchmark + test document generator)
- `scripts/generate_doc.py` — **new file** (end-to-end generation script, Llama 3.1 8B)
- `scripts/generate_llama70b_doc.py` — **new file** (end-to-end generation script, Llama 3.3 70B)
- `tests/generation/test_citation_formatter.py` — 31 tests, all passing
- `tests/generation/test_formatting_engine.py` — 34 tests, all passing

**What changed:**

**Checkpoint test results (65/65 passing):**
- All `Citation_Formatter` unit tests pass — normalize, collect, assign, replace, build index, full pipeline
- All `Formatting_Engine` unit tests pass — party detection, operative clauses, marker parsing, heuristic fallback, PDF HTML injection

**Latency verification (200 iterations, P95):**

| Feature | P95 latency | Limit | Result |
|---|---|---|---|
| Citation formatting | 0.12ms | 50ms | ✅ PASS |
| Underlining / PDF-HTML | 0.91ms | 100ms | ✅ PASS |

Both are well within budget — citation formatting is ~400× faster than the limit.

**Model-agnostic architecture — key design decision:**

Both the citation and underlining pipelines are **post-processing steps** that run on the final text output of any LLM. They do not depend on the model following formatting instructions correctly.

**Citations:**
- `collect_citations()` parses `[CITE:Act, Year, Section]` markers from whatever text the model produces
- If a model emits no markers, the index shows `(No citations found)` — no crash
- If a model emits malformed markers, they become `[UNGROUNDED — MANUAL REVIEW REQUIRED]`
- Works identically with Llama 3.1 8B, Llama 3.3 70B, Qwen 2.5 7B, Gemma 4 9B, or any future model

**Underlining:**
- All `<UNDERLINE>` tag output from the LLM is **stripped entirely** before processing (`_strip_underline_markers()` in `generate_doc.py`)
- Underlining is applied exclusively by the **heuristic regex detector** — independent of model output quality
- The detector runs on the final assembled text, not on per-slot LLM output
- Works identically regardless of which model generated the text

**Specific bugs fixed during checkpoint validation:**

1. **LLM produces corrupted `<UNDERLINE>` tag fragments** (e.g. `LINE>`, `ERLINE>`, `JoshiINE>`) — fixed by stripping all tag remnants before processing and switching to heuristic-only underlining

2. **PDF `<u>` tags being XML-escaped** — `_build_pdf_story` was calling `_escape_xml()` after `underline_parties_pdf_html()`, converting `<u>` to `&lt;u&gt;`. Fixed by escaping non-underlined text segments only, leaving `<u>...</u>` tags intact for reportlab

3. **Bare titled names not detected** (e.g. `Shri Vikram Anand Kulkarni` without a preceding role keyword) — fixed by adding `_TITLED_NAME_PATTERN` to `detect_party_names()` which matches any `Shri/Smt/Mr/Mrs/Dr + capitalized name sequence` anywhere in the text

4. **PDF citation anchor broken links** — `_build_pdf_story` was generating `<a href="#citation_1">` links even when `document.citations=[]` (Phase 1.5 mode), causing reportlab to fail. Fixed by only linkifying when `document.citations` is populated; Phase 1.5 embeds the citation index as plain body text

5. **Groq 8B model 413 Payload Too Large** — cache context capped at 6000 chars for 8B model (vs 24000 for 70B) to stay within the smaller context window

**`src/generation/formatting_engine.py` changes:**
- Added `_TITLED_NAME_PATTERN` — compiled regex matching `Shri/Smt/Mr/Mrs/Dr + N capitalized words`
- `detect_party_names()` now also runs `_TITLED_NAME_PATTERN` over the full text, appending matches with role `"named_party"`
- `get_underline_ranges()` heuristic path now includes operative clauses when `parties_only=False`

**`src/generation/exporter.py` changes:**
- `_build_pdf_story()` body loop: underline injection now happens **before** XML escaping — `<u>` tags are preserved; only non-underlined text segments are passed through `_escape_xml()`
- Citation anchor section: skipped entirely when `document.citations=[]` (Phase 1.5 mode) — no broken `AnchorFlowable` references

**Deploy notes:**
- No new dependencies
- No new environment variables
- The `_strip_underline_markers()` function in `scripts/generate_doc.py` should be applied to any integration that calls `_call_groq()` or `_call_ollama()` directly — strip LLM tag output before passing text to the formatting pipeline
- The heuristic detector is the authoritative source for underlining; LLM `<UNDERLINE>` instructions are advisory only and should not be relied upon for correctness

---

---

### [Task 6] Implement `Model_Registry` — 2026-04-10

**Files changed:**
- `src/model_comparison/model_registry.py` — **new file**
- `src/model_comparison/__init__.py` — **new file** (empty package marker)

**What changed:**

New module providing the central registry of all supported LLM models, RAM-based
availability filtering, and backend factory.

**Dataclasses:**

| Class | Purpose |
|---|---|
| `ModelConfig` | Per-model config: `model_id`, `display_name`, `provider`, `api_endpoint`, `parameter_count`, `context_window`, `required`, `ram_required_gb` |
| `ParameterPreset` | Sampling params: `name`, `temperature`, `top_p`, `max_tokens` |

**`MODEL_REGISTRY` — 6 models:**

| Key | Model | Provider | Required | RAM |
|---|---|---|---|---|
| `llama-3.1-8b` | Llama 3.1 8B Instant | Groq | ✅ | 0 GB (cloud) |
| `qwen-2.5-7b` | Qwen 2.5 7B | Ollama | ✅ | 8 GB |
| `gemma-4-9b` | Gemma 4 9B | Ollama | ✅ | 10 GB |
| `llama-3.3-70b` | Llama 3.3 70B Versatile | Groq | ❌ optional | 0 GB (cloud) |
| `qwen-3-32b` | Qwen 3 32B | Ollama | ❌ optional | 20 GB |
| `gemma-4-31b` | Gemma 4 31B Instruct | Ollama | ❌ optional | 20 GB |

**`PARAMETER_PRESETS` — 2 presets:**

| Name | temperature | top_p | max_tokens |
|---|---|---|---|
| `high_quality` | 0.1 | 0.9 | 1000 |
| `fast` | 0.3 | 0.95 | 600 |

**Public functions:**

| Function | Purpose |
|---|---|
| `detect_system_ram_gb()` | Returns total RAM in whole GB via `psutil.virtual_memory()` |
| `get_available_models(include_large)` | Returns required models only, or all models that fit in available RAM |
| `get_backend(model_id)` | Returns `Groq_Backend` or `Ollama_Backend` instance for the given model key |

**Deploy notes:**
- Requires `psutil` (already added in Task 1)
- No new environment variables — uses `GROQ_API_KEY` (existing) for Groq models
- `get_backend()` uses deferred imports to avoid circular dependency with `backends.py`

---

### [Task 7] Implement Groq and Ollama backends — 2026-04-10

**Files changed:**
- `src/model_comparison/backends.py` — **new file**

**What changed:**

New module implementing the two inference backends used by the model comparison
pipeline. Both expose the same `generate()` interface.

**Custom exceptions:**

| Exception | Raised when |
|---|---|
| `ModelTimeoutError` | API call exceeds 60-second timeout |
| `ModelAPIError` | HTTP 5xx, network error, or rate limit retries exhausted |

**`Groq_Backend.generate(fact_pattern, model_config, preset)`:**
- Calls `{api_endpoint}/chat/completions` (OpenAI-compatible)
- Auth via `GROQ_API_KEY` environment variable
- Returns `(document_text, latency_seconds, token_count)`
- Token count from `usage.total_tokens` in response

**`Ollama_Backend.generate(fact_pattern, model_config, preset)`:**
- Calls `{api_endpoint}/api/chat` (Ollama HTTP API, `stream=false`)
- Returns `(document_text, latency_seconds, token_count)`
- Token count = `prompt_eval_count + eval_count`

**Shared error handling (both backends):**
- HTTP 429 → exponential backoff: 2s → 4s → 8s, max 3 retries; raises `ModelAPIError` if exhausted
- Timeout (>60s) → raises `ModelTimeoutError`
- HTTP 5xx or network error → raises `ModelAPIError` immediately (no retry)

**Deploy notes:**
- No new dependencies — uses `requests` (existing Phase 1 dependency)
- Groq backend requires `GROQ_API_KEY` in environment
- Ollama backend requires Ollama running at `http://localhost:11434` with target models pulled

---

### [Task 8] Implement `Model_Comparator` — 2026-04-10

**Files changed:**
- `src/model_comparison/comparator.py` — **new file**

**What changed:**

New module orchestrating the full M × P × D comparison loop. Runs all
(model, preset, doc_type) combinations sequentially, evaluates each result,
and aggregates statistics into a `ComparisonReport`.

**Design decision — sequential execution:**

The original spec called for `ThreadPoolExecutor(max_workers=3)` but this was
changed to sequential execution for the following reasons:

- Ollama loads one model at a time internally — concurrent requests queue up
  and are processed serially anyway, so threads add overhead without benefit
- Running multiple Ollama models simultaneously would require loading all of
  them into RAM at once (e.g. Qwen 7B + Gemma 9B = ~18 GB simultaneously on a
  16 GB machine), causing OOM kills or heavy swap usage
- Groq calls are fast (~3–5s) and rate-limited — sequential is fine
- Sequential execution is easier to reason about, debug, and resume after failure

**Dataclasses:**

| Class | Fields |
|---|---|
| `ComparisonRun` | `run_id`, `timestamp`, `model_id`, `parameter_preset`, `doc_type`, `fact_pattern`, `generated_text`, `evaluation_result`, `latency_seconds`, `token_count`, `backend` |
| `ComparisonReport` | `runs`, `failed_runs`, `summary`, `best_models`, `total_runs`, `successful_runs`, `total_duration_seconds` |

**Public API:**

| Function | Purpose |
|---|---|
| `run_comparison(models, presets, doc_types, fact_patterns, output_dir)` | Main entry point — runs all combinations, returns `ComparisonReport` |

**Internal helpers:**

| Function | Purpose |
|---|---|
| `_run_single(model_key, preset_name, doc_type, fact_pattern, output_dir)` | Executes one combination; returns `ComparisonRun` on success or failure `dict` |
| `_evaluate(...)` | Calls `evaluate_cag_document()` (Tier 1+2) + optionally `evaluate_ragas()` (Tier 3) |
| `_make_generated_document(...)` | Wraps raw LLM text in a minimal `GeneratedDocument` stub for the evaluator |
| `_write_intermediate(run, output_dir)` | Writes `output_dir/intermediate/<model>__<preset>__<doc_type>__<run_id[:8]>.json` immediately after each run |
| `_compute_summary(runs)` | Computes mean/median/std/min/max per metric per model-preset combo |
| `_identify_best_models(summary)` | Returns best model-preset combo per metric (highest mean) |

**Evaluation integration (subtask 8.3):**
- `_evaluate()` calls `evaluate_cag_document()` for Tier 1+2 metrics unconditionally
- Tier 3 RAGAS is called only when `GROQ_RAGAS_API_KEY` (or `GROQ_API_KEY`) is set
- RAGAS uses an empty cache stub (`_EmptyCache`) since model comparison runs outside the CAG cache pipeline
- Any evaluation failure returns `{}` — non-fatal, run is still recorded

**Error resilience:**
- `ModelTimeoutError` → run skipped, added to `failed_runs`, loop continues
- `ModelAPIError` → run skipped, added to `failed_runs`, loop continues
- Any unexpected exception → logged at ERROR level, skipped, loop continues
- Evaluation failures → `evaluation_result = {}`, run still recorded as successful

**Progress tracking:**
- `tqdm` progress bar with `unit="run"`, `set_postfix(model=..., doc_type=...)` updated before each run

**Deploy notes:**
- No new dependencies beyond `tqdm` (added in Task 1)
- No new environment variables
- `GROQ_RAGAS_API_KEY` controls Tier 3 evaluation (optional — falls back to Tier 1+2 only)
- Intermediate results written to `{output_dir}/intermediate/` — safe to delete after a successful full run
- Gated by `ENABLE_MODEL_COMPARISON=true` feature flag (enforced by the CLI in Task 10, not by this module directly)

## Current Status

| Task | Status | Description |
|---|---|---|
| 1 | ✅ Complete | Environment setup and feature flags |
| 2 | ✅ Complete | `Citation_Formatter` (`src/generation/citation_formatter.py`) |
| 3 | ✅ Complete | `Formatting_Engine` (`src/generation/formatting_engine.py`) |
| 4 | ✅ Complete | Prompt updates + pipeline integration |
| 5 | ✅ Complete | Checkpoint — 65/65 tests pass, latency verified, model-agnostic hardening done |
| 6 | ✅ Complete | `Model_Registry` (`src/model_comparison/model_registry.py`) |
| 7 | ✅ Complete | Groq + Ollama backends (`src/model_comparison/backends.py`) |
| 8 | ✅ Complete | `Model_Comparator` (`src/model_comparison/comparator.py`) — subtasks 8.1, 8.3 |
| 9 | ✅ Complete | `Comparison_Reporter` (`src/model_comparison/reporter.py`) — subtasks 9.1, 9.3 |
| 10 | ✅ Complete | Model comparison CLI (`scripts/run_model_comparison.py`) |
| 11–12 | ⏳ Pending | Area 2: test suite, checkpoint |
| 13–18 | ⏳ Pending | Area 3: Chat Backend |
| 19–21 | ⏳ Pending | Area 4: Documentation and Deployment |

---

## Cloud Deployment Checklist (Area 1 — Citations & Formatting)

These steps are sufficient to deploy the completed Area 1 features to a cloud environment.

### 1. Dependencies

```bash
pip install -r requirements.txt
```

New packages added in Phase 1.5 so far: `psutil==5.9.8`, `tqdm==4.66.4`.
All other dependencies (`python-docx`, `reportlab`, `requests`) were already present.

### 2. Environment variables

Set the following in your cloud secret manager / environment config.
All have safe defaults — only override what you need to change.

```bash
# Master switch — set false to revert entirely to Phase 1 behaviour
ENABLE_PHASE_1_5=true

# Area 1: Citations and Formatting
ENABLE_CITATION_FORMATTING=true   # end-of-document [N] citation index
ENABLE_UNDERLINE_FORMATTING=true  # party name underlining in DOCX/PDF

# These are not yet active but should be set to false until Area 2/3 are deployed
ENABLE_MODEL_COMPARISON=false
ENABLE_CHAT_BACKEND=false
```

> **Tip:** If you only want to deploy Area 1 now and keep Areas 2 and 3 off,
> set `ENABLE_MODEL_COMPARISON=false` and `ENABLE_CHAT_BACKEND=false`.

### 3. New files to deploy

| File | Type | Required |
|---|---|---|
| `src/generation/citation_formatter.py` | New | Yes (if `ENABLE_CITATION_FORMATTING=true`) |
| `src/generation/formatting_engine.py` | New | Yes (if `ENABLE_UNDERLINE_FORMATTING=true`) |

### 4. Modified files to deploy

| File | Change summary |
|---|---|
| `config/settings.py` | Phase 1.5 settings fields + `log_feature_flags()` |
| `src/cag/engine.py` | Updated `_build_slot_prompt()` — new citation/underline instructions |
| `src/generation/document_generator.py` | `generate_document()` — Citation_Formatter integration |
| `src/generation/exporter.py` | `export_docx()` + `_build_pdf_story()` — Formatting_Engine integration |
| `.env.example` | Phase 1.5 env var documentation |
| `requirements.txt` | Added `psutil`, `tqdm` |

### 5. Verify at startup

Add this to your app entrypoint if not already present:

```python
from config.settings import settings
settings.log_feature_flags()
```

Expected log output with Area 1 enabled:
```
=== Phase 1.5 Feature Flags ===
  ENABLE_PHASE_1_5            = True
  ENABLE_CITATION_FORMATTING  = True
  ENABLE_UNDERLINE_FORMATTING = True
  ENABLE_MODEL_COMPARISON     = False
  ENABLE_CHAT_BACKEND         = False
```

### 6. Rollback

To roll back any individual feature without redeployment:

```bash
# Roll back citation formatting only
ENABLE_CITATION_FORMATTING=false

# Roll back underlining only
ENABLE_UNDERLINE_FORMATTING=false

# Roll back everything
ENABLE_PHASE_1_5=false
```

No code changes or restarts required beyond the env var update (assuming your
platform supports live env var injection, e.g. AWS ECS task definition update,
GCP Cloud Run revision, or Heroku config vars).

---

## Cloud Deployment Checklist (Area 2 — SLM Model Comparison, partial)

Tasks 6, 7, and 8 are complete. Tasks 9 (Reporter), 10 (CLI), 11 (tests), and 12 (checkpoint) are pending.
The comparator module is functional but not yet exposed via CLI — do not deploy Area 2 to production until Task 10 is complete.

### New files added (Tasks 6–8)

| File | Type |
|---|---|
| `src/model_comparison/__init__.py` | New |
| `src/model_comparison/model_registry.py` | New |
| `src/model_comparison/backends.py` | New |
| `src/model_comparison/comparator.py` | New |

### Environment variables (Area 2)

```bash
# Feature flag (set in Task 1)
ENABLE_MODEL_COMPARISON=true
MODEL_COMPARISON_OUTPUT_DIR=./output/model_comparison
INCLUDE_LARGE_MODELS=false          # set true only on machines with ≥32 GB RAM

# Required for Groq-backed models (llama-3.1-8b, llama-3.3-70b)
GROQ_API_KEY=your-groq-api-key

# Optional — enables Tier 3 RAGAS evaluation during comparison runs
GROQ_RAGAS_API_KEY=your-groq-api-key   # can be same as GROQ_API_KEY
```

### Ollama setup (for Qwen/Gemma models)

```bash
ollama pull qwen2.5:7b
ollama pull gemma4:9b

# Optional large models (only if INCLUDE_LARGE_MODELS=true and RAM ≥ 32 GB)
ollama pull qwen3:32b
ollama pull gemma4:31b-instruct
```

---

### [Task 9] Implement `Comparison_Reporter` — 2026-04-10

**Files changed:**
- `src/model_comparison/reporter.py` — **new file**

**What changed:**

New module exporting model comparison results in CSV, JSON, and Markdown formats.

**Public API:**

| Function | Purpose |
|---|---|
| `export_csv(report, output_path)` | One row per run; appends to existing file for incremental experiments |
| `export_summary_csv(report, output_path)` | One row per model-preset combo; mean, std, median per metric |
| `export_json(report, output_path)` | Nested `{model_id: {preset: {doc_type: [runs]}}}` + summary + best_models + failed_runs |
| `compute_aggregate_stats(runs)` | Mean/median/std/n per metric per model-preset combo |
| `identify_best_models(summary)` | Best combo per metric (highest mean) |
| `generate_summary_markdown(report, output_path)` | Full Markdown report with executive summary, per-metric tables, latency comparison, failed runs, recommendations |

**CSV columns (`results.csv`):** `timestamp`, `model_id`, `parameter_preset`, `doc_type`, `run_id`, `backend`, `latency_seconds`, `token_count`, then all Tier 1/2/3 metric columns.

**Markdown report sections:** Executive Summary table → Tier 1 metrics table → Tier 2 metrics table → Tier 3 RAGAS table (if present) → Latency Comparison → Failed Runs → Recommendations.

**Deploy notes:**
- No new dependencies — stdlib only (`csv`, `json`, `statistics`)
- Output files written to `MODEL_COMPARISON_OUTPUT_DIR` (set in Task 1)

---

### [Task 10] Implement model comparison CLI — 2026-04-10

**Files changed:**
- `scripts/run_model_comparison.py` — **new file**

**What changed:**

CLI script that wires together `Model_Registry`, `Model_Comparator`, and `Comparison_Reporter` into a single runnable command.

**Arguments:**

| Argument | Default | Description |
|---|---|---|
| `--models` | `all` | Model keys or `all` (3 required models) |
| `--presets` | `high_quality fast` | One or both presets |
| `--doc-types` | `all` | Doc type keys or `all` (7 types) |
| `--output-dir` | `MODEL_COMPARISON_OUTPUT_DIR` | Results directory |
| `--include-large-models` | false | Include 70B+ optional models |

**Behaviour:**
- Gated by `ENABLE_MODEL_COMPARISON=true` — exits with error if disabled
- Resolves `--models all` via `get_available_models()` (respects RAM detection)
- Includes built-in minimal fact patterns for all 7 document types
- Runs `run_comparison()` → writes `results.csv`, `summary.csv`, `results.json`, `summary.md`
- Prints a compact summary table to stdout on completion (SFR, FFid, SecC, Latency per combo)
- Exits with code 1 if all runs failed (CI-friendly)

**Example usage:**
```bash
# All required models, all doc types
python scripts/run_model_comparison.py --models all --doc-types all

# Quick test: one model, one doc type
python scripts/run_model_comparison.py --models llama-3.1-8b --doc-types sale_deed --presets fast

# Include large models (needs ≥32 GB RAM for Ollama large models)
python scripts/run_model_comparison.py --models all --include-large-models
```

**Deploy notes:**
- Requires `GROQ_API_KEY` for Llama models
- Requires Ollama running locally with `qwen2.5:7b` and `gemma4:9b` pulled for Ollama models
- `ENABLE_MODEL_COMPARISON=true` must be set (or `ENABLE_PHASE_1_5=true` with default flags)

---

### [Task 10 — Post-run fixes] Model comparison pipeline hardening — 2026-04-10

**Files changed:**
- `scripts/run_model_comparison.py` — added `load_dotenv()` at startup; fixed duplicate `sys.path` insert; replaced Unicode chars with ASCII in summary table
- `src/model_comparison/backends.py` — added `think: False` to Ollama payload; added `thinking` field fallback; added HTTP 401 handler with helpful message
- `src/model_comparison/reporter.py` — fixed `UnboundLocalError` in `_metric_table` nested function (`lines +=` → `lines.extend()`)
- `src/evaluation/evaluator.py` — updated `section_completeness` to use heading groups with alternative patterns

**What changed:**

**Issue 1 — GROQ_API_KEY not loaded from `.env`**
- Root cause: `scripts/run_model_comparison.py` used `os.environ` directly via `config/settings.py`, but `python-dotenv` was never called
- Fix: added `from dotenv import load_dotenv; load_dotenv()` at the top of the CLI script, before any imports that read env vars
- Result: Tier A (Groq) models now authenticate correctly

**Issue 2 — Qwen 3.5 models returning empty `generated_text`**
- Root cause: Qwen 3.5 models (4B and 397B MoE) have thinking mode enabled by default. When thinking mode is active, Ollama returns `message.content = ""` and puts the model's output in `message.thinking` instead
- Fix 1: Added `"think": False` to the Ollama `/api/chat` payload — disables thinking mode, response goes to `content` as expected
- Fix 2: Added defensive fallback — if `content` is still empty after the call, extract from `message.thinking` field
- Result: Qwen 3.5 4B and 397B MoE Cloud now produce non-empty `generated_text`

**Issue 3 — `section_completeness = 0.0` for all models except Llama 3.3 70B**
- Root cause: The evaluator checked for exact headings (`PARTIES`, `RECITALS`, `OPERATIVE CLAUSE`, `SCHEDULE`, `ATTESTATION`). Only Llama 3.3 70B happened to produce `SCHEDULE` — all other models use equivalent but differently-worded headings (e.g. `BY AND BETWEEN`, `WHEREAS`, `NOW THIS DEED WITNESSETH`, `IN WITNESS WHEREOF`)
- Fix: Replaced single-string heading list with heading groups — each group contains all equivalent patterns for that section. Any match in the group counts as that section being present
- Heading groups:
  - Parties: `PARTIES`, `BY AND BETWEEN`, `BETWEEN:`, party role keywords
  - Recitals: `RECITALS`, `WHEREAS`, `BACKGROUND`
  - Operative clause: `OPERATIVE CLAUSE`, `NOW THIS DEED WITNESSETH`, `IT IS HEREBY AGREED`, `WITNESSETH`
  - Schedule: `SCHEDULE`, `PROPERTY DESCRIPTION`, `DESCRIPTION OF PROPERTY`
  - Attestation: `ATTESTATION`, `IN WITNESS WHEREOF`, `SIGNED`, `WITNESSES`
- Result: All models now receive credit for sections they actually include

**Issue 4 — `UnboundLocalError` in `reporter.py`**
- Root cause: `_metric_table` nested function used `lines +=` (augmented assignment), which Python treats as a local variable assignment, making `lines` unbound in the closure
- Fix: Changed `lines += [...]` to `lines.extend([...])` — mutates the outer list without rebinding the name

**Full pipeline run results (post-fix, sale_deed, 12/12 successful):**

| Model / Preset | Tier | SFR | FFid | SecC | Lat(s) |
|---|---|---|---|---|---|
| llama-3.1-8b/fast | A-Baseline | 1.000 | 0.750 | — | 1.45 |
| llama-3.1-8b/high_quality | A-Baseline | 1.000 | 0.625 | — | 1.72 |
| llama-3.3-70b/fast | A-Baseline | 1.000 | 0.875 | 0.200 | 2.65 |
| llama-3.3-70b/high_quality | A-Baseline | 1.000 | 0.875 | 0.200 | 3.86 |
| gemma-4-e4b/fast | B-Small | 1.000 | 0.000 | — | 10.09 |
| gemma-4-e4b/high_quality | B-Small | 1.000 | 0.625 | — | 19.61 |
| qwen-3.5-4b/fast | B-Small | 1.000 | 0.000 | — | 12.55 |
| qwen-3.5-4b/high_quality | B-Small | 1.000 | 0.000 | — | 22.70 |
| gemma-4-31b-cloud/fast | C-LargeCloud | 1.000 | 0.750 | — | 18.86 |
| gemma-4-31b-cloud/high_quality | C-LargeCloud | 1.000 | 0.750 | — | 42.83 |
| qwen-3.5-397b-cloud/fast | C-LargeCloud | 1.000 | 0.000 | — | 9.69 |
| qwen-3.5-397b-cloud/high_quality | C-LargeCloud | 1.000 | 0.000 | — | 10.64 |

*Note: `section_completeness` fix applied — next run will show updated values for all models.*

**Deploy notes:**
- `python-dotenv` must be installed (already in `requirements.txt`)
- No new environment variables
- Re-run `python scripts/run_model_comparison.py --models all --doc-types sale_deed` to get updated metrics with all fixes applied

---

### [Task 10 — Pipeline hardening round 2] Rate limiting, timeouts, output quality — 2026-04-10

**Files changed:**
- `src/model_comparison/backends.py` — multi-key rotation, smart wait, structured prompt, token limits raised
- `src/model_comparison/comparator.py` — per-model cooldown between consecutive Groq runs
- `src/model_comparison/model_registry.py` — `max_tokens` raised for both presets
- `scripts/run_model_comparison.py` — all 7 metrics in summary table; PDF + JSON document saving; Pydantic warning suppressed

**What changed:**

**Groq rate limit handling (multi-key rotation):**
- `_load_groq_api_keys()` reads `GROQ_API_KEY`, `GROQ_API_KEY_2`, ..., `GROQ_API_KEY_9` — add extra free-tier keys to `.env` for automatic rotation
- On HTTP 429: rotates to next key immediately (2s pause); only waits for quota refresh after all keys exhausted
- Wait on all-keys-exhausted: uses `Retry-After` header, capped at 120s (prevents 22-minute hangs from daily quota headers)
- `_MAX_RETRIES` raised to 8; backoff sequence extended to `[2, 4, 8, 15, 30, 60, 90, 120]`
- `_GROQ_INTER_REQUEST_DELAY = 2.0s` — small pause after every successful Groq call to stay under RPM
- Comparator adds 8s cooldown between consecutive runs of the same 70B Groq model (3s for 8B)

**Ollama timeout fix:**
- `_OLLAMA_TIMEOUT_SECONDS = 180` (raised from 60s) — cloud Ollama models (gemma4:31b-cloud) need up to 2-3 minutes for longer document types

**Output quality — structured system prompt:**
- Replaced generic system prompt with an explicit 7-section scaffold:
  1. Document title and execution date
  2. PARTIES section
  3. RECITALS / WHEREAS clauses
  4. OPERATIVE CLAUSE (NOW THIS DEED WITNESSETH)
  5. Numbered material clauses
  6. SCHEDULE
  7. ATTESTATION / IN WITNESS WHEREOF
- Explicit "Do NOT truncate" instruction added
- User prompt now says "Begin the document now:" to prevent preamble
- Impact: smaller models (Gemma 4 E4B, Qwen 3.5 4B) produce more complete documents with correct section headings, improving `section_completeness` scores

**Token limits raised:**
- `high_quality`: 1000 → 3000 tokens
- `fast`: 600 → 2000 tokens
- Previous limits were truncating documents mid-generation, causing 0.000 scores on fact fidelity and section completeness

**Summary table — all 7 metrics:**
- Table now shows: CHR, SFR, FFid, SecC, CitF, JurA, Lat(s)
- Best-per-metric section shows abbreviated name + full name + tier label

**Document saving (PDF + JSON):**
- After each run, generated documents saved to `output/model_comparison/documents/<model>__<preset>__<doc_type>__<run_id[:8]>/`
- `document.json` — full run metadata + generated text + evaluation result
- `document.pdf` — reportlab-rendered PDF with title, metadata block, and document body
- Control characters stripped before PDF rendering to prevent reportlab failures

**Pydantic warning suppressed:**
- `warnings.filterwarnings("ignore", message="Core Pydantic V1 functionality")` added at CLI startup — removes the LangChain/Python 3.14 compatibility noise from every run

**RAGAS / evaluator log cleanup:**
- `ragas not installed` warning → `debug` level
- `No cache content available for RAGAS` warning → `debug` level
- Both are expected in model comparison context (no CAG cache available)

**Deploy notes:**
- Add `GROQ_API_KEY_2`, `GROQ_API_KEY_3`, etc. to `.env` for rate limit resilience (each free Groq account has independent quota)
- `datasets`, `appdirs`, `instructor` added to `requirements.txt` — required by RAGAS on Python 3.14
- `sentence-transformers` added — provides `all-MiniLM-L6-v2` embeddings for RAGAS
- `slowapi` added — required for chat backend rate limiting (Area 3)
- Run `pip install -r requirements.txt` to pick up all new dependencies

---

## Current Status (updated)

| Task | Status | Description |
|---|---|---|
| 1 | ✅ Complete | Environment setup and feature flags |
| 2 | ✅ Complete | `Citation_Formatter` |
| 3 | ✅ Complete | `Formatting_Engine` |
| 4 | ✅ Complete | Prompt updates + pipeline integration |
| 5 | ✅ Complete | Checkpoint — 65/65 tests pass |
| 6 | ✅ Complete | `Model_Registry` — updated to 3-tier pipeline (6 required models) |
| 7 | ✅ Complete | Groq + Ollama backends — multi-key rotation, 180s Ollama timeout |
| 8 | ✅ Complete | `Model_Comparator` |
| 9 | ✅ Complete | `Comparison_Reporter` |
| 10 | ✅ Complete | Model comparison CLI — full 7-metric table, PDF/JSON doc saving |
| 11 | ✅ Complete | Model comparison test suite |
| 12 | ✅ Complete | Checkpoint — 12/12 runs successful (sale_deed), all tiers validated |
| 13–18 | ⏳ Pending | Area 3: Chat Backend |
| 19–21 | ⏳ Pending | Area 4: Documentation and Deployment |

---

## Quality of Life & Cloud Deployment Improvements (recommended next steps)

### QoL improvements

1. **Resume interrupted runs** — add `--resume` flag to CLI that reads existing `intermediate/` files and skips already-completed combinations. Useful when a run is interrupted mid-way through all 7 doc types.

2. **`--doc-types` progress persistence** — currently results.csv appends on every run, which can create duplicate rows if the same combination is re-run. Add a run deduplication check keyed on `(model_id, parameter_preset, doc_type, timestamp_date)`.

3. **HTML report** — generate an `index.html` alongside `summary.md` with sortable tables and a latency bar chart using Chart.js (no extra dependencies — pure HTML/JS).

4. **Groq rate limit pre-check** — before starting a run, check remaining quota via Groq's `/openai/v1/models` endpoint and warn if close to daily limit.

---

### [QoL + Cloud] Resume, deduplication, dry-run, JSON logging, Dockerfile — 2026-04-10

**Files changed:**
- `scripts/run_model_comparison.py` — `--resume`, `--dry-run`, `--json-logs` flags; `_load_completed_combinations()`; `_load_runs_from_intermediate()`; `_dry_run_check()`
- `src/model_comparison/comparator.py` — `run_comparison()` accepts `skip_combinations` + `prior_runs`; fixed `total` → `total_planned` NameError
- `src/model_comparison/reporter.py` — `export_csv()` now deduplicates by `run_id`; rewrites file instead of appending; strips extra columns from old rows
- `src/model_comparison/visualizer.py` — **new file** — 5 research-paper figures
- `Dockerfile.model_comparison` — **new file** — one-command cloud deployment
- `.gitignore` — **new file** — comprehensive exclusions
- `.dockerignore` — **new file** — clean Docker build context
- `SECURITY.md` — **new file** — responsible disclosure + secret management guide
- `output/.gitkeep` — **new file** — preserves output directory in git
- `requirements.txt` — added `seaborn`, `matplotlib`, `pandas`, `python-json-logger`

**What changed:**

**`--resume` flag:**
- Scans `output/model_comparison/intermediate/` on startup
- Builds set of `(model_id, preset, doc_type)` already completed
- Skips them in the work list; seeds report with prior runs
- Safe to re-run after any interruption — no duplicate work

**CSV deduplication:**
- `export_csv()` reads existing file, merges rows by `run_id`, rewrites
- Re-running the same doc type no longer creates duplicate rows
- Extra columns from old runs (e.g. `grounded_citations`) are stripped to keep schema consistent

**`--dry-run` flag:**
- Validates all Groq API keys via `/openai/v1/models`
- Checks Ollama reachability at `localhost:11434`
- Reports which local models are pulled vs missing
- Exits 0 on pass, 1 on any failure — use before committing to a 2-hour run

**`--json-logs` flag:**
- Switches to `python-json-logger` format (one JSON object per line)
- Compatible with CloudWatch, Stackdriver, Datadog
- Falls back to plain text if `python-json-logger` not installed

**5 research-paper visualizations (`src/model_comparison/visualizer.py`):**
Generated automatically after every pipeline run, saved to `output/model_comparison/visuals/`:

| File | Description |
|---|---|
| `quality_heatmap.png` | Metric heatmap — all models × all quality metrics |
| `latency_comparison.png` | Grouped bar chart by tier, both presets |
| `quality_radar.png` | Spider chart comparing tier quality profiles |
| `metric_bars.png` | Per-metric grouped bars across all models |
| `fact_fidelity_dist.png` | Violin + strip distribution per model |

Style: seaborn `whitegrid`, 300 DPI, tier-coloured (blue=Groq, red=Small, green=Cloud), print-safe palette.

**`Dockerfile.model_comparison`:**
- Python 3.11-slim base
- Installs all dependencies from `requirements.txt`
- Copies only `config/`, `src/`, `scripts/run_model_comparison.py`
- Default CMD: `--models all --doc-types all --json-logs`
- Usage examples in header for Groq-only, full pipeline, dry-run, JSON logs

**Security hardening for GitHub push:**
- `.gitignore` — excludes `.env`, `output/`, `*.db`, `*.gguf`, `__pycache__/`, dataset directories
- `.dockerignore` — excludes secrets, datasets, dev artifacts from Docker builds
- `SECURITY.md` — responsible disclosure policy, secret management guide, API key rotation instructions
- Confirmed: no hardcoded API keys in any source file (scan clean)

**Deploy notes:**
- `pip install python-json-logger` for JSON logging (or use `--json-logs` flag which auto-falls-back)
- `pip install seaborn matplotlib pandas` for visualizations (already in `requirements.txt`)
- Rotate Groq API keys before pushing to GitHub (keys were visible in `.env` during development)
- `output/` directory is gitignored — mount as a Docker volume in production

---

### [Area 3 Scope Decision] Chat backend simplified for cloud deployment — 2026-04-10

**Files changed:**
- `.kiro/specs/phase-1-5-enhancements/tasks.md` — tasks 13-18 rewritten with simplified scope

**What changed:**

Tasks 13-18 (Chat Backend) were redesigned from a complex multi-component system to a lean, cloud-deploy-first FastAPI service.

**Removed from scope:**
- Redis session storage + docker-compose redis service (in-memory is sufficient for research demo)
- `slowapi` rate limiting middleware (handle at cloud gateway: AWS ALB, GCP Cloud Armor, Nginx)
- `/admin/sessions` endpoint + API key authentication
- All property tests for chat backend (4 optional tasks)
- Sub-task nesting (14.1, 14.2, 14.3... → flat tasks)

**Kept in scope:**
- FastAPI app with `/health`, `/docs`, CORS, JSON request logging
- In-memory session manager with 30-min TTL and background purge
- Schema-driven question generator (mirrors existing Streamlit intake flow)
- `/api/chat/start`, `/api/chat/respond`, `/api/chat/revise`
- Input sanitization (strip control chars, truncate to 1000 chars)
- `Dockerfile.chat` for one-command cloud deployment
- Basic integration test suite (6 tests, <30s)

**Rationale:**
The original tasks 13-18 had 12 sub-tasks across 6 parent tasks. The simplified version has 6 flat tasks. The core Q&A functionality is identical — the removed items were infrastructure complexity that adds no research value and complicates cloud deployment.

**Deploy notes:**
- No new dependencies beyond `fastapi` and `uvicorn` (to be added in task 13)
- Single `Dockerfile.chat` — no docker-compose changes needed
- Rate limiting: configure at the load balancer level (e.g. AWS WAF, GCP Cloud Armor)

---

## Current Status (updated)

| Task | Status | Description |
|---|---|---|
| 1 | ✅ Complete | Environment setup and feature flags |
| 2 | ✅ Complete | `Citation_Formatter` |
| 3 | ✅ Complete | `Formatting_Engine` |
| 4 | ✅ Complete | Prompt updates + pipeline integration |
| 5 | ✅ Complete | Checkpoint — 65/65 tests pass |
| 6 | ✅ Complete | `Model_Registry` — 3-tier pipeline (6 required models) |
| 7 | ✅ Complete | Groq + Ollama backends — multi-key rotation, 180s timeout |
| 8 | ✅ Complete | `Model_Comparator` |
| 9 | ✅ Complete | `Comparison_Reporter` |
| 10 | ✅ Complete | Model comparison CLI — resume, dry-run, JSON logs, visualizations |
| 11 | ✅ Complete | Model comparison test suite |
| 12 | ✅ Complete | Checkpoint — 12/12 runs successful, all tiers validated |
| 13 | ⏳ Pending | FastAPI app skeleton + `Dockerfile.chat` |
| 14 | ⏳ Pending | `Session_Manager` (in-memory) |
| 15 | ⏳ Pending | `Question_Generator` |
| 16 | ⏳ Pending | Chat API endpoints |
| 17 | ⏳ Pending | Chat backend test suite |
| 18 | ⏳ Pending | Checkpoint — Chat Backend |
| 19–21 | ⏳ Pending | Area 4: Documentation and Deployment |
