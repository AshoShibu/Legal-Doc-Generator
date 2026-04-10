# Requirements Document: Phase 1.5 Enhancements

## Introduction

Phase 1.5 enhances the Maharashtra Legal Document Generation System with three critical improvements: (1) citation and formatting refinements for professional document output, (2) small language model (SLM) comparison infrastructure for research paper novelty, and (3) conversational chat-based backend for improved user experience. These enhancements maintain backward compatibility with the existing Phase 1 CAG pipeline while adding research-grade evaluation capabilities and production-ready deployment infrastructure.

## Glossary

- **System**: The Maharashtra Legal Document Generation System (Phase 1.5)
- **Citation_Formatter**: Component responsible for citation collection and end-of-document listing
- **Formatting_Engine**: Component that applies underlining to clauses and party names
- **Model_Comparator**: Component that executes document generation across multiple SLM configurations
- **Evaluation_Framework**: The existing three-tier evaluation system (CAG-specific, structural/legal, RAGAS)
- **Chat_Backend**: Conversational interface backend for guided document generation
- **Groq_API**: Cloud LLM inference service (primary backend)
- **Hugging_Face_API**: Alternative model hosting service for SLM access
- **Deployment_Endpoint**: REST API endpoint ready for cloud deployment
- **SLM**: Small Language Model (parameter count ≤ 10B)
- **Document_Type**: One of seven supported legal document types (Sale Deed, Mortgage Deed, Power of Attorney, Leave and License, Gift Deed, Conveyance Deed, Affidavit)

## Requirements

### Requirement 1: Citation Reference System

**User Story:** As a legal professional, I want citations listed methodically at the end of documents rather than inline for every generation, so that I can review all legal sources in one consolidated section without disrupting document readability.

#### Acceptance Criteria

1. WHEN a document is generated, THE Citation_Formatter SHALL collect all citations during generation and append them as a numbered reference list at the end of the document body
2. THE Citation_Formatter SHALL assign sequential reference numbers to citations in order of first appearance in the document
3. WHEN a citation appears multiple times in the document, THE Citation_Formatter SHALL reuse the same reference number for all occurrences
4. THE System SHALL insert inline citation markers in the format `[N]` where N is the reference number
5. THE Citation_Formatter SHALL format the end-of-document citation list with the heading "CITATION INDEX" followed by numbered entries
6. FOR ALL citations in the index, THE Citation_Formatter SHALL include: act name, year, section number, court/legislature, and judgment year in the format `[Act Name, Year] Section X(Y), [Court/Legislature], [Year]`
7. THE Citation_Formatter SHALL apply this citation system to all 7 Document_Types without requiring template modifications

### Requirement 2: Document Formatting Standards

**User Story:** As a legal professional, I want clauses and party names underlined in all generated documents, so that the output conforms to Maharashtra legal drafting conventions.

#### Acceptance Criteria

1. WHEN a document contains party names (Vendor, Purchaser, Mortgagor, Mortgagee, Principal, Attorney, Licensor, Licensee, Donor, Donee, Conveyor, Transferee, Deponent), THE Formatting_Engine SHALL apply underlining to each occurrence
2. WHEN a document contains operative clauses (sections beginning with "NOW THIS DEED WITNESSETH", "IT IS HEREBY AGREED", "THE PARTIES AGREE"), THE Formatting_Engine SHALL apply underlining to the clause heading
3. THE Formatting_Engine SHALL preserve underlining in both DOCX and PDF export formats
4. THE Formatting_Engine SHALL apply formatting rules to all 7 Document_Types
5. WHEN a party name appears in the PARTIES section, THE Formatting_Engine SHALL underline the full name including titles (e.g., "Shri Rajesh Kumar Sharma")
6. THE Formatting_Engine SHALL NOT underline citation markers, section headings, or the document header

### Requirement 3: System Prompt Enhancement

**User Story:** As a system administrator, I want system prompts updated to guarantee citation and formatting compliance, so that all generated documents meet professional standards without manual post-processing.

#### Acceptance Criteria

1. THE System SHALL update the slot-level generation prompts in `_build_slot_prompt()` to include explicit citation reference instructions
2. THE System SHALL instruct the LLM to output citation markers in the format `[CITE:Act Name, Year, Section X(Y)]` during generation
3. THE Citation_Formatter SHALL parse `[CITE:...]` markers and replace them with sequential reference numbers `[N]`
4. THE System SHALL update prompts to instruct the LLM to output party names in the format `<UNDERLINE>Party Name</UNDERLINE>` during generation
5. THE Formatting_Engine SHALL parse `<UNDERLINE>...</UNDERLINE>` markers and apply underlining in the final document
6. WHEN the LLM fails to output formatting markers, THE Formatting_Engine SHALL apply heuristic detection (regex-based party name detection) as a fallback
7. THE System SHALL validate that updated prompts do not increase average generation latency by more than 10%

### Requirement 4: SLM Model Integration

**User Story:** As a researcher, I want to compare document generation quality across multiple small language models (≤10B parameters), so that I can quantify the impact of model architecture on legal document quality for the research paper while maintaining accessibility on standard hardware.

#### Acceptance Criteria

1. THE Model_Comparator SHALL support five models with ≤10B parameters: Llama 3.1 8B (already implemented), Qwen 2.5 7B, and Gemma 4 9B as required models, with Llama 3.3 70B and larger variants (Qwen 32B, Gemma 31B) as optional models for high-memory systems
2. THE Model_Comparator SHALL access Llama 3.1 8B via Groq_API as the primary backend
3. THE Model_Comparator SHALL access Qwen 2.5 7B and Gemma 4 9B via local Ollama inference
4. THE Model_Comparator SHALL configure each model with two parameter presets: "high quality" (temperature=0.1, top_p=0.9, max_tokens=1000) and "fast" (temperature=0.3, top_p=0.95, max_tokens=600)
5. THE Model_Comparator SHALL execute document generation for a given fact pattern across all required model-parameter combinations (3 required models × 2 presets = 6 configurations minimum, up to 6 models × 2 presets = 12 configurations with optional models)
6. THE Model_Comparator SHALL record generation latency, token count, model identifier, and inference backend (Groq/Ollama) for each configuration
7. THE Model_Comparator SHALL pass each generated document to the Evaluation_Framework for quality metric computation
8. THE Model_Comparator SHALL detect available system RAM and automatically enable/disable optional large models (≥32GB RAM required for 70B+ models)

### Requirement 5: Model Comparison Evaluation

**User Story:** As a researcher, I want evaluation metrics computed for each SLM configuration, so that I can compare model performance quantitatively and identify the optimal model-parameter combination for the research paper.

#### Acceptance Criteria

1. WHEN the Model_Comparator completes generation for all configurations, THE Evaluation_Framework SHALL compute Tier 1 metrics (cache_hit_rate, slot_fill_rate, fact_fidelity_score, latency_seconds) for each configuration
2. THE Evaluation_Framework SHALL compute Tier 2 metrics (section_completeness, citation_format_compliance, jurisdictional_accuracy) for each configuration
3. THE Evaluation_Framework SHALL compute Tier 3 RAGAS metrics (faithfulness, answer_relevance, context_precision, context_recall) for each configuration
4. THE Model_Comparator SHALL export results as a CSV file with columns: model_name, parameter_preset, doc_type, run_id, and all metric values
5. THE Model_Comparator SHALL export results as a JSON file with the same data in structured format
6. THE Model_Comparator SHALL compute aggregate statistics (mean, median, std) per model-parameter combination across all 7 Document_Types
7. THE Model_Comparator SHALL generate a comparison report highlighting the top-performing configuration for each metric

### Requirement 6: Model Comparison CLI

**User Story:** As a researcher, I want a command-line interface to run model comparison experiments, so that I can execute batch evaluations without manual intervention.

#### Acceptance Criteria

1. THE System SHALL provide a CLI script `scripts/run_model_comparison.py` that accepts arguments: `--models`, `--presets`, `--doc-types`, `--output-dir`, `--include-large-models`
2. WHEN invoked with `--models all`, THE CLI SHALL run comparison across all 3 required models (≤10B parameters)
3. WHEN invoked with `--models all --include-large-models`, THE CLI SHALL run comparison across all 6 models (including 70B+ variants) if system RAM ≥32GB
4. WHEN invoked with `--doc-types all`, THE CLI SHALL run comparison across all 7 Document_Types
5. THE CLI SHALL display a progress bar showing: current model, current document type, completed runs, estimated time remaining
6. THE CLI SHALL write intermediate results to disk after each model-document combination completes
7. WHEN the CLI encounters a model API error or insufficient RAM, THE CLI SHALL log the error, skip that configuration, and continue with remaining configurations
8. THE CLI SHALL output a summary table to stdout showing metric averages per model upon completion

### Requirement 7: Chat-Based Interface Backend

**User Story:** As a user, I want to interact with the system through a conversational interface that asks follow-up questions based on my document type, so that I can provide information incrementally rather than filling a long form upfront.

#### Acceptance Criteria

1. THE Chat_Backend SHALL expose a REST API endpoint `/api/chat/start` that accepts a document type and returns an initial question
2. WHEN a user selects a Document_Type, THE Chat_Backend SHALL return the first required field from the corresponding intake schema as a natural language question
3. THE Chat_Backend SHALL expose a REST API endpoint `/api/chat/respond` that accepts a user answer and returns the next question or a completion signal
4. WHEN the user provides an answer, THE Chat_Backend SHALL validate the answer against the field type (text, number, date, select) and return an error message if invalid
5. WHEN all required fields are collected, THE Chat_Backend SHALL return a completion signal with the assembled fact pattern
6. THE Chat_Backend SHALL maintain conversation state in a session store (Redis or in-memory dict) keyed by session_id
7. THE Chat_Backend SHALL support conditional questions (e.g., "Is the property encumbered?" → if yes, ask encumbrance details)

### Requirement 8: Chat Backend Question Generation

**User Story:** As a user, I want the chat interface to ask clear, context-aware questions in natural language, so that I understand what information is needed without legal jargon.

#### Acceptance Criteria

1. THE Chat_Backend SHALL convert intake schema field labels to natural language questions using a question template system
2. WHEN a field has a `help` attribute in the schema, THE Chat_Backend SHALL include the help text in the question
3. THE Chat_Backend SHALL format questions based on field type: text → "Please provide...", number → "Enter the amount...", date → "What is the date of...", select → "Choose one of the following..."
4. WHEN a field has `ocr_source` set, THE Chat_Backend SHALL pre-fill the answer from OCR results and ask "We detected [value]. Is this correct?"
5. THE Chat_Backend SHALL support multi-turn clarification: if the user's answer is ambiguous, THE Chat_Backend SHALL ask a follow-up question
6. THE Chat_Backend SHALL provide example answers for complex fields (e.g., "Example: 123/4A for survey number")
7. THE Chat_Backend SHALL track question history and allow users to revise previous answers via `/api/chat/revise` endpoint

### Requirement 9: Chat Backend Deployment Readiness

**User Story:** As a DevOps engineer, I want the chat backend to be production-ready with clear API documentation and deployment configuration, so that I can deploy it to cloud infrastructure without code modifications.

#### Acceptance Criteria

1. THE Chat_Backend SHALL be implemented as a FastAPI application with OpenAPI documentation at `/docs`
2. THE Chat_Backend SHALL include a `Dockerfile` and `docker-compose.yml` for containerized deployment
3. THE Chat_Backend SHALL expose a health check endpoint `/health` that returns HTTP 200 when the service is ready
4. THE Chat_Backend SHALL log all requests and responses to stdout in JSON format for centralized logging
5. THE Chat_Backend SHALL support CORS configuration via environment variable `CORS_ORIGINS`
6. THE Chat_Backend SHALL rate-limit requests to 100 requests per minute per IP address
7. THE Chat_Backend SHALL include a `README.md` with deployment instructions, environment variable documentation, and API usage examples

### Requirement 10: Chat Backend Session Management

**User Story:** As a system administrator, I want chat sessions to expire after inactivity and be cleanly garbage-collected, so that the backend does not accumulate stale session data.

#### Acceptance Criteria

1. THE Chat_Backend SHALL expire sessions after 30 minutes of inactivity
2. WHEN a session expires, THE Chat_Backend SHALL return HTTP 404 with message "Session expired. Please start a new conversation."
3. THE Chat_Backend SHALL run a background task every 5 minutes to purge expired sessions from the session store
4. THE Chat_Backend SHALL support session persistence: if Redis is configured, sessions SHALL be stored in Redis; otherwise, in-memory dict
5. WHEN using Redis, THE Chat_Backend SHALL set TTL (time-to-live) on session keys to 30 minutes
6. THE Chat_Backend SHALL expose an admin endpoint `/admin/sessions` (protected by API key) that lists active session count and oldest session age
7. THE Chat_Backend SHALL log session creation, expiration, and purge events for monitoring

### Requirement 11: Integration with Existing Pipeline

**User Story:** As a developer, I want Phase 1.5 enhancements to integrate seamlessly with the existing Phase 1 pipeline, so that no breaking changes are introduced to the current system.

#### Acceptance Criteria

1. THE System SHALL maintain backward compatibility: existing CAG pipeline calls SHALL continue to work without modification
2. THE Citation_Formatter SHALL be invoked automatically in `generate_document()` without requiring changes to `app.py`
3. THE Formatting_Engine SHALL be invoked automatically in `export_document()` without requiring changes to `app.py`
4. THE Model_Comparator SHALL be a standalone module that does not modify existing `engine.py` or `document_generator.py` code
5. THE Chat_Backend SHALL be a separate service that does not depend on the Streamlit frontend
6. WHEN Phase 1.5 features are disabled via environment variable `ENABLE_PHASE_1_5=false`, THE System SHALL behave identically to Phase 1
7. THE System SHALL pass all existing Phase 1 test cases after Phase 1.5 integration

### Requirement 12: Model Comparison Test Suite

**User Story:** As a developer, I want automated tests for the model comparison infrastructure, so that I can verify correctness before running expensive multi-model experiments.

#### Acceptance Criteria

1. THE System SHALL provide a test script `tests/test_model_comparison.py` that runs a single-document comparison across 2 models
2. THE test script SHALL use a minimal fact pattern (5 fields) and a single Document_Type (Sale Deed) to minimize execution time
3. THE test script SHALL verify that all 3 required models (≤10B parameters) are accessible via Groq API or Ollama
4. THE test script SHALL verify that the Evaluation_Framework computes all Tier 1+2 metrics for each model
5. THE test script SHALL verify that the output CSV contains the expected columns and row count
6. THE test script SHALL complete in under 5 minutes on a machine with 16GB RAM and Groq API access
7. THE test script SHALL skip RAGAS evaluation (Tier 3) to avoid LLM judge latency
8. THE test script SHALL skip optional large models (70B+) unless `--include-large-models` flag is provided

### Requirement 13: Chat Backend Test Suite

**User Story:** As a developer, I want automated tests for the chat backend API, so that I can verify conversation flow and error handling before deployment.

#### Acceptance Criteria

1. THE System SHALL provide a test script `tests/test_chat_backend.py` that exercises all API endpoints
2. THE test script SHALL verify the `/api/chat/start` endpoint returns a valid question for each Document_Type
3. THE test script SHALL verify the `/api/chat/respond` endpoint advances through all required fields and returns a completion signal
4. THE test script SHALL verify that invalid answers (wrong type, out-of-range) return HTTP 400 with descriptive error messages
5. THE test script SHALL verify that expired sessions return HTTP 404
6. THE test script SHALL verify that the `/health` endpoint returns HTTP 200
7. THE test script SHALL complete in under 30 seconds

### Requirement 14: Documentation Updates

**User Story:** As a new developer or researcher, I want comprehensive documentation for Phase 1.5 features, so that I can understand the system architecture and run experiments without prior knowledge.

#### Acceptance Criteria

1. THE System SHALL include a `docs/phase-1-5-overview.md` document describing all three enhancement areas
2. THE document SHALL include architecture diagrams for: citation flow, model comparison pipeline, chat backend request flow
3. THE document SHALL include a "Quick Start" section with commands to run model comparison and start the chat backend
4. THE document SHALL include a "Research Paper Integration" section explaining how to use model comparison results
5. THE document SHALL include API reference documentation for all Chat_Backend endpoints with request/response examples
6. THE document SHALL include a troubleshooting section covering common errors (API key issues, model unavailability, session expiration)
7. THE document SHALL be written in Markdown and include a table of contents

### Requirement 15: Environment Configuration

**User Story:** As a system administrator, I want all Phase 1.5 features configurable via environment variables, so that I can deploy the system in different environments (development, staging, production) without code changes.

#### Acceptance Criteria

1. THE System SHALL read the following environment variables: `ENABLE_PHASE_1_5`, `ENABLE_CITATION_FORMATTING`, `ENABLE_UNDERLINE_FORMATTING`, `ENABLE_MODEL_COMPARISON`, `ENABLE_CHAT_BACKEND`
2. WHEN `ENABLE_PHASE_1_5=false`, THE System SHALL disable all Phase 1.5 features
3. THE System SHALL read `MODEL_COMPARISON_OUTPUT_DIR` to specify where comparison results are written (default: `./output/model_comparison`)
4. THE System SHALL read `CHAT_BACKEND_PORT` to specify the FastAPI server port (default: 8000)
5. THE System SHALL read `CHAT_SESSION_STORE` to specify session storage backend: `redis` or `memory` (default: memory)
6. WHEN `CHAT_SESSION_STORE=redis`, THE System SHALL read `REDIS_URL` for connection details
7. THE System SHALL include an updated `.env.example` file documenting all new environment variables

### Requirement 16: Performance Benchmarking

**User Story:** As a researcher, I want to measure the performance impact of Phase 1.5 enhancements, so that I can report overhead in the research paper.

#### Acceptance Criteria

1. THE System SHALL provide a benchmark script `scripts/benchmark_phase_1_5.py` that measures generation latency with and without Phase 1.5 features
2. THE benchmark script SHALL run 10 generations per Document_Type with Phase 1.5 enabled and 10 with Phase 1.5 disabled
3. THE benchmark script SHALL compute mean latency, median latency, and 95th percentile latency for each configuration
4. THE benchmark script SHALL output a comparison table showing latency overhead per feature (citation formatting, underlining, model comparison)
5. THE benchmark script SHALL verify that citation formatting adds no more than 50ms overhead per document
6. THE benchmark script SHALL verify that underlining adds no more than 100ms overhead per document (DOCX export only)
7. THE benchmark script SHALL complete in under 10 minutes on a machine with Groq API access

### Requirement 17: Error Handling and Resilience

**User Story:** As a user, I want the system to handle API failures gracefully during model comparison, so that a single model failure does not abort the entire experiment.

#### Acceptance Criteria

1. WHEN a model API call fails with HTTP 429 (rate limit), THE Model_Comparator SHALL retry with exponential backoff (2s, 4s, 8s) up to 3 times
2. WHEN a model API call fails with HTTP 500 (server error), THE Model_Comparator SHALL skip that configuration and log the error
3. WHEN a model API call times out after 60 seconds, THE Model_Comparator SHALL skip that configuration and log the timeout
4. THE Model_Comparator SHALL continue with remaining configurations after a failure
5. THE Model_Comparator SHALL include a "failed_runs" section in the output JSON listing all skipped configurations with error messages
6. THE Chat_Backend SHALL return HTTP 503 (Service Unavailable) when the LLM backend is unreachable, with a retry-after header
7. THE Chat_Backend SHALL validate all user inputs and return HTTP 400 (Bad Request) with descriptive error messages for invalid inputs

### Requirement 18: Model Comparison Output Format

**User Story:** As a researcher, I want model comparison results in a format suitable for statistical analysis and visualization, so that I can generate plots and tables for the research paper.

#### Acceptance Criteria

1. THE Model_Comparator SHALL export results as a CSV file with one row per model-document-run combination
2. THE CSV SHALL include columns: timestamp, model_name, parameter_preset, doc_type, run_id, cache_hit_rate, slot_fill_rate, fact_fidelity_score, section_completeness, citation_format_compliance, jurisdictional_accuracy, latency_seconds, token_count
3. THE Model_Comparator SHALL export results as a JSON file with nested structure: `{model_name: {parameter_preset: {doc_type: [run_results]}}}`
4. THE Model_Comparator SHALL generate a summary CSV with one row per model-parameter combination showing mean and std for each metric
5. THE Model_Comparator SHALL generate a Markdown report with tables comparing models on each metric
6. THE Model_Comparator SHALL include a "best_model" field in the JSON output identifying the top-performing model per metric
7. THE Model_Comparator SHALL support appending results to existing CSV files for incremental experiments

### Requirement 19: Chat Backend Security

**User Story:** As a security engineer, I want the chat backend to implement basic security controls, so that it is safe to deploy on public cloud infrastructure.

#### Acceptance Criteria

1. THE Chat_Backend SHALL validate all input strings to prevent injection attacks (SQL, NoSQL, command injection)
2. THE Chat_Backend SHALL sanitize user inputs before passing to the LLM (strip control characters, limit length to 1000 characters)
3. THE Chat_Backend SHALL implement rate limiting: 100 requests per minute per IP address, 1000 requests per hour per IP address
4. THE Chat_Backend SHALL return HTTP 429 (Too Many Requests) when rate limits are exceeded
5. THE Chat_Backend SHALL require an API key for admin endpoints (`/admin/sessions`) via `X-API-Key` header
6. THE Chat_Backend SHALL log all failed authentication attempts (invalid API key, rate limit exceeded)
7. THE Chat_Backend SHALL support HTTPS in production via environment variable `ENABLE_HTTPS=true`

### Requirement 20: Rollback and Feature Flags

**User Story:** As a system administrator, I want to disable Phase 1.5 features individually via feature flags, so that I can roll back problematic features without redeploying the entire system.

#### Acceptance Criteria

1. THE System SHALL check `ENABLE_CITATION_FORMATTING` before invoking the Citation_Formatter
2. WHEN `ENABLE_CITATION_FORMATTING=false`, THE System SHALL use the Phase 1 inline citation behavior
3. THE System SHALL check `ENABLE_UNDERLINE_FORMATTING` before invoking the Formatting_Engine
4. WHEN `ENABLE_UNDERLINE_FORMATTING=false`, THE System SHALL export documents without underlining
5. THE System SHALL check `ENABLE_MODEL_COMPARISON` before allowing access to the model comparison CLI
6. THE System SHALL check `ENABLE_CHAT_BACKEND` before starting the FastAPI server
7. THE System SHALL log feature flag states at startup for debugging

## Special Requirements Guidance

### Parser and Serializer Requirements

This system does not introduce new parsers or serializers in Phase 1.5. The existing OCR pipeline (`src/ocr/pipeline.py`) and intake schema loader (`src/intake/form_renderer.py`) continue to handle parsing. No round-trip properties are required for Phase 1.5.

### Property-Based Testing Guidance

**Requirement 4 (SLM Model Integration)** and **Requirement 5 (Model Comparison Evaluation)** are suitable for property-based testing:

- **Property**: For any valid fact pattern and any supported model, generation SHALL produce a document with `slot_fill_rate >= 0.7` (at least 70% of slots filled)
- **Property**: For any two models generating from the same fact pattern, the citation sets SHALL overlap by at least 50% (both models cite similar statutes)
- **Property**: For any model configuration, `latency_seconds` SHALL be less than 120 seconds for documents with ≤ 7 slots

**Requirement 7 (Chat Backend)** is suitable for property-based testing:

- **Property**: For any sequence of valid user answers, the chat backend SHALL eventually reach a completion state (no infinite loops)
- **Property**: For any Document_Type, the number of questions asked SHALL equal the number of required fields in the intake schema

**Requirements 1-3 (Citation and Formatting)** are deterministic transformations and are better tested with example-based unit tests rather than property-based tests.

**Requirement 9 (Deployment Readiness)** involves infrastructure configuration and is tested via integration tests (health checks, CORS, rate limiting) rather than property-based tests.
