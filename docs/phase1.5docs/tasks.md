# Implementation Plan: Phase 1.5 Enhancements

## Overview

Implement three enhancements to the Maharashtra Legal Document Generation System in priority order:
1. **Citations and Formatting** — end-of-document citation index + underlined clauses/parties
2. **SLM Model Comparison** — 3-tier research evaluation: Llama 3.1 8B + 3.3 70B (Groq baseline), Gemma 4 E4B + Qwen 3.5 4B (small model viability), Gemma 4 31B Cloud + Qwen 3.5 397B MoE Cloud (large model viability)
3. **Chat Backend** — FastAPI conversational interface for guided document generation

All enhancements are gated by feature flags and maintain full backward compatibility with Phase 1.

---

## Tasks

- [x] 1. Environment setup and feature flag infrastructure
  - Add all Phase 1.5 environment variables to `.env.example` and `config/settings.py`
  - Implement feature flag loader: `ENABLE_PHASE_1_5`, `ENABLE_CITATION_FORMATTING`, `ENABLE_UNDERLINE_FORMATTING`, `ENABLE_MODEL_COMPARISON`, `ENABLE_CHAT_BACKEND`
  - Log all feature flag states at startup
  - Add `psutil` and `tqdm` to `requirements.txt` (needed for RAM detection and progress bars)
  - _Requirements: 15.1, 15.2, 15.7, 20.1–20.7_

---

## Area 1: Citations and Formatting

- [x] 2. Implement `Citation_Formatter` (`src/generation/citation_formatter.py`)
  - [x] 2.1 Implement citation marker parsing and reference number assignment
    - Write `collect_citations(text)` — parse `[CITE:Act, Year, Section X]` markers, return `(text, list[CitationMarker])`
    - Write `assign_reference_numbers(citations)` — assign sequential numbers in order of first appearance
    - Write `normalize_citation_key(act, year, section)` — lowercase, strip, pipe-delimited key
    - Handle duplicate citations: same key → same reference number
    - _Requirements: 1.2, 1.3, 3.2, 3.3_

  - [ ]* 2.2 Write property test for citation reference number assignment
    - **Property 1: Citation Reference Number Assignment** — for N citations, numbers are 1..N in first-appearance order; duplicates reuse same number
    - **Property 4: Citation Marker Transformation** — `[CITE:...]` markers replaced with `[N]`, all other text unchanged
    - **Validates: Requirements 1.2, 1.3, 3.2, 3.3**

  - [x] 2.3 Implement citation index builder and marker replacement
    - Write `replace_markers(text, citation_map)` — replace `[CITE:...]` with `[N]`
    - Write `build_citation_index(citations)` — format per Indian Legal Citation Standard, include "Cited in clause(s):" line
    - Output heading: `--- CITATION INDEX ---`
    - Handle zero-citation case: append "(No citations found in this document.)"
    - Handle malformed markers: log warning, mark as `[UNGROUNDED — MANUAL REVIEW REQUIRED]`
    - _Requirements: 1.1, 1.4, 1.5, 1.6_

  - [ ]* 2.4 Write property test for citation index structure
    - **Property 2: Citation Index Structure** — index has "CITATION INDEX" heading, numbered entries with act name, year, section, court/legislature
    - **Property 3: Inline Citation Marker Format** — all inline markers match `[N]` pattern where N is a positive integer
    - **Validates: Requirements 1.1, 1.4, 1.5, 1.6**

- [x] 3. Implement `Formatting_Engine` (`src/generation/formatting_engine.py`)
  - [x] 3.1 Implement party name and operative clause detection
    - Write `detect_party_names(text, doc_type)` — regex patterns for all 7 document types (Vendor/Purchaser, Mortgagor/Mortgagee, Principal/Attorney, Licensor/Licensee, Donor/Donee, Conveyor/Transferee, Deponent)
    - Write `detect_operative_clauses(text)` — detect "NOW THIS DEED WITNESSETH", "IT IS HEREBY AGREED", "THE PARTIES AGREE", "IN CONSIDERATION WHEREOF"
    - Write `parse_underline_markers(text)` — parse `<UNDERLINE>...</UNDERLINE>` markers
    - Compile all regex patterns once at module load time
    - _Requirements: 2.1, 2.2, 3.4, 3.5_

  - [ ]* 3.2 Write property test for party name and operative clause underlining
    - **Property 5: Party Name Underlining** — all party name occurrences underlined; citation markers, section headings, document header NOT underlined
    - **Property 6: Operative Clause Underlining** — all operative clause headings underlined
    - **Property 8: Underline Marker Transformation** — `<UNDERLINE>...</UNDERLINE>` markers parsed and underlining applied
    - **Validates: Requirements 2.1, 2.2, 2.5, 2.6, 3.4, 3.5**

  - [x] 3.3 Implement DOCX and PDF underlining application
    - Write `apply_underline_docx(doc, ranges)` — apply underlining to character ranges in python-docx `Document`
    - Write `apply_underline_pdf(pdf_content, ranges)` — apply underlining in reportlab
    - Implement heuristic fallback: if no `<UNDERLINE>` markers found, run `detect_party_names` + `detect_operative_clauses`
    - Fall back to plain export (no underlining) if DOCX/PDF underlining fails; log warning
    - _Requirements: 2.3, 2.4, 3.6_

  - [ ]* 3.4 Write property test for formatting preservation and heuristic fallback
    - **Property 7: Formatting Preservation Across Export Formats** — underlining present in both DOCX and PDF outputs
    - **Property 9: Heuristic Formatting Fallback** — when no `<UNDERLINE>` markers present, heuristic detection applies underlining
    - **Validates: Requirements 2.3, 2.4, 3.6**

- [x] 4. Update system prompts and integrate formatters into pipeline
  - Update `_build_slot_prompt()` in `src/cag/engine.py` to instruct LLM to output `[CITE:Act Name, Year, Section X(Y)]` markers
  - Update prompts to instruct LLM to output `<UNDERLINE>Party Name</UNDERLINE>` markers
  - Integrate `Citation_Formatter` into `generate_document()` in `src/generation/document_generator.py` — gated by `ENABLE_CITATION_FORMATTING`
  - Integrate `Formatting_Engine` into `export_document()` in `src/generation/exporter.py` — gated by `ENABLE_UNDERLINE_FORMATTING`
  - Verify no changes required in `app.py` (automatic invocation)
  - _Requirements: 3.1, 3.2, 3.4, 11.2, 11.3, 20.1–20.4_

  - [ ]* 4.1 Write unit tests for prompt integration and automatic invocation
    - Test that `Citation_Formatter` is called automatically when `ENABLE_CITATION_FORMATTING=true`
    - Test that `Formatting_Engine` is called automatically when `ENABLE_UNDERLINE_FORMATTING=true`
    - Test that neither is called when flags are `false` (backward compatibility)
    - **Property 10: Cross-Document-Type Consistency** — citation and formatting rules apply uniformly to all 7 document types
    - **Property 33: Backward Compatibility with Feature Flags** — Phase 1 output identical when `ENABLE_PHASE_1_5=false`
    - **Property 34: Automatic Integration** — formatters invoked without explicit calls in `app.py`
    - **Validates: Requirements 1.7, 2.4, 11.1, 11.2, 11.3, 11.6, 20.1–20.4**

- [x] 5. Checkpoint — Citations and Formatting
  - Ensure all citation and formatting tests pass, ask the user if questions arise.
  - Verify latency overhead: citation formatting ≤50ms, underlining ≤100ms per document.
co
---

## Area 2: SLM Model Comparison

- [x] 6. Implement `Model_Registry` (`src/model_comparison/model_registry.py`)
  - Define `ModelConfig` and `ParameterPreset` dataclasses
  - Populate `MODEL_REGISTRY` with the 6 required pipeline models across 3 tiers:
    - **Tier A (Groq baseline)**: Llama 3.1 8B (`llama-3.1-8b-instant`), Llama 3.3 70B (`llama-3.3-70b-versatile`)
    - **Tier B (small model viability, Ollama local)**: Gemma 4 E4B (`gemma4:e4b`), Qwen 3.5 4B (`qwen3.5:4b`)
    - **Tier C (large model viability, Ollama cloud)**: Gemma 4 31B Cloud (`gemma4:31b-cloud`), Qwen 3.5 397B MoE Cloud (`qwen3.5:397b-cloud`)
  - All other models (qwen-2.5-7b, gemma-4-9b, qwen-3-32b, gemma-4-31b) remain in registry as `required=False`
  - Define `PARAMETER_PRESETS`: `high_quality` (temp=0.1, top_p=0.9, max_tokens=1000) and `fast` (temp=0.3, top_p=0.95, max_tokens=600)
  - Implement `detect_system_ram_gb()` using `psutil.virtual_memory()`
  - Implement `get_available_models(include_large)` — filter by RAM and `required` flag
  - Implement `get_backend(model_id)` — return `Groq_Backend` or `Ollama_Backend` instance
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.8_

  - [ ]* 6.1 Write property test for RAM-based model availability
    - **Property 13: RAM-Based Model Availability** — models with `ram_required_gb > R` are disabled; models with `ram_required_gb ≤ R` are enabled
    - **Validates: Requirements 4.8**

- [x] 7. Implement Groq and Ollama backends (`src/model_comparison/backends.py`)
  - Implement `Groq_Backend.generate(fact_pattern, model_config, preset)` — call Groq OpenAI-compatible API, return `(document_text, latency_seconds, token_count)`
  - Implement `Ollama_Backend.generate(fact_pattern, model_config, preset)` — call Ollama HTTP API at `http://localhost:11434`, return same tuple
  - Implement exponential backoff retry for HTTP 429: 2s → 4s → 8s, max 3 retries
  - Implement 60-second timeout; on timeout, raise `ModelTimeoutError`
  - Skip configuration on HTTP 500 or network error; raise `ModelAPIError`
  - _Requirements: 4.2, 4.3, 17.1, 17.2, 17.3_

- [x] 8. Implement `Model_Comparator` (`src/model_comparison/comparator.py`)
  - [x] 8.1 Implement comparison orchestration with parallel execution
    - Write `run_comparison(models, presets, doc_types, fact_patterns, output_dir)` — execute all M × P × D combinations
    - Use `ThreadPoolExecutor(max_workers=3)` for parallel execution
    - Track progress with `tqdm` progress bar showing current model and doc type
    - Write intermediate results to disk after each (model, preset, doc_type) completes
    - Collect `ComparisonRun` dataclass for each successful run
    - Populate `failed_runs` list for skipped configurations with error messages
    - _Requirements: 4.5, 4.6, 4.7, 6.5, 6.6, 6.7, 17.4, 17.5_

  - [ ]* 8.2 Write property test for model-parameter combination execution and error resilience
    - **Property 11: Model-Parameter Combination Execution** — all M × P × D combinations executed; latency, token count, model ID, backend recorded
    - **Property 17: CLI Incremental Output** — intermediate results written after each combination
    - **Property 18: Error Resilience in Model Comparison** — failed runs logged and skipped; remaining configurations continue
    - **Validates: Requirements 4.5, 4.6, 6.6, 17.1–17.5**

  - [x] 8.3 Integrate evaluation framework into comparison runs
    - Call `evaluate_cag_document()` (Tier 1+2) for each generated document
    - Call RAGAS evaluation (Tier 3) if `GROQ_RAGAS_API_KEY` is set; skip otherwise
    - Populate `ComparisonRun.evaluation_result` with all metric values; use `null` for failed evaluations
    - _Requirements: 4.7, 5.1, 5.2, 5.3_

  - [ ]* 8.4 Write property test for comprehensive evaluation coverage
    - **Property 12: Comprehensive Evaluation Coverage** — all Tier 1 metrics (cache_hit_rate, slot_fill_rate, fact_fidelity_score, latency_seconds), all Tier 2 metrics (section_completeness, citation_format_compliance, jurisdictional_accuracy) computed for each run
    - **Validates: Requirements 4.7, 5.1, 5.2, 5.3**

- [x] 9. Implement `Comparison_Reporter` (`src/model_comparison/reporter.py`)
  - [x] 9.1 Implement CSV and JSON export
    - Write `export_csv(report, output_path)` — one row per run, all required columns (timestamp, model_id, parameter_preset, doc_type, run_id, all metrics, latency_seconds, token_count, backend)
    - Write `export_summary_csv(report, output_path)` — one row per model-parameter combination, mean and std per metric
    - Write `export_json(report, output_path)` — nested structure `{model_name: {parameter_preset: {doc_type: [run_results]}}}`
    - Support appending to existing CSV files for incremental experiments
    - _Requirements: 5.4, 5.5, 18.1, 18.2, 18.3, 18.4, 18.7_

  - [ ]* 9.2 Write property test for export completeness
    - **Property 14: Comparison Results Export Completeness** — CSV and JSON contain exactly N rows/entries for N successful runs; all required columns populated
    - **Validates: Requirements 5.4, 5.5, 18.1, 18.2, 18.3**

  - [x] 9.3 Implement aggregate statistics and Markdown summary report
    - Write `compute_aggregate_stats(runs)` — mean, median, std per model-parameter combination per metric
    - Write `identify_best_models(summary)` — top-performing configuration per metric
    - Write `generate_summary_markdown(report, output_path)` — executive summary table, per-metric comparison tables, latency comparison, failed runs section, recommendations
    - _Requirements: 5.6, 5.7, 18.5, 18.6_

  - [ ]* 9.4 Write property test for aggregate statistics and best model identification
    - **Property 15: Aggregate Statistics Computation** — mean, median, std computed for each metric across all 7 document types per model-parameter combination
    - **Property 16: Best Model Identification** — top-performing configuration identified per metric based on mean values
    - **Validates: Requirements 5.6, 5.7**

- [x] 10. Implement model comparison CLI (`scripts/run_model_comparison.py`)
  - Accept CLI arguments: `--models`, `--presets`, `--doc-types`, `--output-dir`, `--include-large-models`
  - `--models all` → all 6 required pipeline models (Tier A + B + C)
  - `--models all --include-large-models` → all 10 models including optional ones
  - `--doc-types all` → all 7 document types
  - Display `tqdm` progress bar with current model, doc type, completed runs, ETA
  - Write intermediate results after each combination; print summary table to stdout on completion
  - Summary table groups results by tier (Baseline / Small / Large Cloud) for easy comparison
  - Log errors and skip failed configurations; continue with remaining
  - Gate entire CLI behind `ENABLE_MODEL_COMPARISON` feature flag
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 20.5_

- [x] 11. Write model comparison test suite (`tests/test_model_comparison.py`)
  - Test single-document comparison across 2 models (Llama 3.1 8B + Gemma 4 E4B), 1 doc type (Sale Deed), minimal 5-field fact pattern
  - Verify all 6 required pipeline models are registered (Tier A: 2 Groq, Tier B: 2 Ollama local, Tier C: 2 Ollama cloud)
  - Verify Tier 1+2 metrics computed for each model
  - Verify output CSV has expected columns and row count
  - Skip RAGAS (Tier 3) to minimize execution time
  - Skip optional models unless `--include-large-models` flag provided
  - Must complete in under 5 minutes on 16GB RAM with Groq API access
  - _Requirements: 12.1–12.8_

- [x] 12. Checkpoint — SLM Model Comparison
  - Ensure all model comparison tests pass, ask the user if questions arise.
  - Verify CLI runs end-to-end with `--models all --doc-types sale_deed`.

---

## Area 3: Chat Backend (Simplified — Cloud-Deploy-First)

> **Scope decision:** Simplified to in-memory sessions only, no Redis, no rate limiting middleware
> (handle at cloud gateway/load balancer), no admin endpoints, no property tests.
> Single `Dockerfile.chat` for one-command cloud deployment.
> Core Q&A flow driven by existing intake schemas — same logic as the Streamlit frontend.

- [ ] 13. Set up FastAPI application skeleton (`src/chat_backend/app.py`)
  - Create FastAPI app with OpenAPI docs at `/docs`
  - Implement `/health` endpoint returning `{"status": "healthy", "timestamp": "..."}`
  - Configure CORS via `CORS_ORIGINS` environment variable (comma-separated list)
  - Configure JSON request/response logging to stdout (one line per request: method, path, status, latency)
  - Gate server startup behind `ENABLE_CHAT_BACKEND` feature flag — exit with clear error if disabled
  - Create `Dockerfile.chat` for standalone cloud deployment
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 20.6_

- [ ] 14. Implement `Session_Manager` (`src/chat_backend/session_manager.py`)
  - Write `create_session(doc_type, ocr_fields)` — UUID session_id, load intake schema, initialize `ChatSession`
  - Write `get_session(session_id)` — return `None` if expired (>30 min inactive) or not found
  - Write `update_session(session)` — refresh `last_activity` timestamp
  - Write `store_answer(session_id, field_id, answer)` — store answer, advance `current_field_index`
  - Write `purge_expired_sessions(ttl_minutes=30)` — remove stale sessions; return purge count
  - Background purge task every 5 minutes via `asyncio.create_task`
  - `threading.Lock` for thread safety
  - Log session creation, expiration, and purge events
  - _Requirements: 10.1, 10.2, 10.3, 10.7_

- [ ] 15. Implement `Question_Generator` (`src/chat_backend/question_generator.py`)
  - Write `generate_question(field, doc_type, ocr_fields)` — convert intake schema field to `Question` dataclass
  - Type-specific templates:
    - `text` → "Please provide {label}"
    - `number` → "Enter the {label} (numeric value)"
    - `date` → "What is the {label}? (format: DD/MM/YYYY)"
    - `select` → "Choose the {label} from the following options:"
    - `multiselect` → "Select all applicable {label}:"
    - `boolean` → "Is {label}? (yes/no)"
  - Include `help` text when field has `help` attribute
  - Include example answers for complex fields (survey_number, gat_number)
  - Write `format_confirmation_question(field_label, ocr_value)` — "We detected [value]. Is this correct?"
  - Write `get_next_question(session)` — advance to next unanswered required field, skip conditionals
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

- [ ] 16. Implement chat API endpoints (`src/chat_backend/routes.py`)
  - `POST /api/chat/start` — create session, return `session_id` and first question
  - `POST /api/chat/respond` — validate answer, store in session, return next question or completion signal
    - Validate by field type: text (max 1000 chars), number (numeric), date (DD/MM/YYYY), select (must be in options)
    - HTTP 400 with descriptive error for invalid answers
    - HTTP 404 with "Session expired. Please start a new conversation." for expired sessions
    - `{"status": "complete", "fact_pattern": {...}}` when all required fields collected
  - `POST /api/chat/revise` — update answer for `field_id`, regenerate question sequence from that point
  - Input sanitization on all endpoints: strip control characters (ASCII 0–31 except `\n`, `\t`), truncate to 1000 chars
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 8.7, 19.1, 19.2_

- [ ] 17. Write chat backend test suite (`tests/test_chat_backend.py`)
  - Test `/health` returns HTTP 200
  - Test `/api/chat/start` returns valid first question for each of the 7 document types
  - Test `/api/chat/respond` advances through all required fields and returns completion signal
  - Test invalid answers return HTTP 400 with descriptive messages
  - Test expired sessions return HTTP 404
  - Must complete in under 30 seconds (no real LLM calls needed)
  - _Requirements: 13.1–13.7_

- [ ] 18. Checkpoint — Chat Backend
  - Ensure all chat backend tests pass.
  - Verify `/health` endpoint and session expiration work correctly.
  - Confirm `Dockerfile.chat` builds and starts cleanly.

---

## Area 4: Documentation and Deployment

- [ ] 19. Write Phase 1.5 documentation (`docs/phase-1-5-overview.md`)
  - Architecture diagrams for citation flow, model comparison pipeline, chat backend request flow
  - "Quick Start" section: commands to run model comparison and start chat backend
  - "Research Paper Integration" section: how to use model comparison results
  - API reference for all Chat_Backend endpoints with request/response examples
  - Troubleshooting section: API key issues, model unavailability, session expiration
  - Table of contents
  - _Requirements: 14.1–14.7_

- [ ] 20. Write benchmark script (`scripts/benchmark_phase_1_5.py`)
  - Run 10 generations per document type with Phase 1.5 enabled and 10 with disabled
  - Compute mean, median, and 95th percentile latency for each configuration
  - Output comparison table: latency overhead per feature (citation formatting, underlining)
  - Verify citation formatting overhead ≤50ms, underlining overhead ≤100ms
  - Must complete in under 10 minutes with Groq API access
  - _Requirements: 16.1–16.7_

- [ ] 21. Final checkpoint — Full integration
  - Run all existing Phase 1 tests with `ENABLE_PHASE_1_5=false` — verify all pass.
  - Run all existing Phase 1 tests with `ENABLE_PHASE_1_5=true` — verify all pass.
  - Ensure all tests pass, ask the user if questions arise.

---

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation at the end of each area
- Property tests validate universal correctness properties; unit tests validate specific examples and edge cases
- The design document uses Python throughout — no language selection needed
- **Pipeline model tiers** (all free):
  - Tier A (Groq baseline): Llama 3.1 8B, Llama 3.3 70B — no local RAM needed
  - Tier B (small model viability): Gemma 4 E4B, Qwen 3.5 4B — ~16 GB RAM peak, already downloaded
  - Tier C (large model viability): Gemma 4 31B Cloud, Qwen 3.5 397B MoE Cloud — no local RAM needed (Ollama-hosted)
- Optional models (qwen-2.5-7b, gemma-4-9b, qwen-3-32b, gemma-4-31b) available via `--include-large-models`
- Feature flags allow individual rollback without redeployment
- **Chat backend (Area 3) is simplified for cloud deployment:**
  - In-memory sessions only — no Redis dependency
  - No rate limiting middleware — handle at cloud gateway (AWS ALB, GCP Cloud Armor, Nginx)
  - No admin endpoints — not needed for research demo
  - Single `Dockerfile.chat` for one-command deployment
  - Schema-driven Q&A mirrors the existing Streamlit intake flow
