"""
src/evaluation/evaluator.py — CAG Evaluation Framework

Computes three tiers of quality metrics for generated legal documents:

  Tier 1 — CAG-specific (no external dependencies, runs on every generation):
    - cache_hit_rate        : grounded citations / total citations
    - slot_fill_rate        : filled slots / total template slots
    - fact_fidelity_score   : intake fields present verbatim in output
    - latency_seconds       : wall-clock generation time

  Tier 2 — Structural/legal quality (regex-based, no LLM needed):
    - section_completeness  : slots with >50 tokens of content / total slots
    - citation_format_compliance : citations matching the standard format / total
    - jurisdictional_accuracy    : citations referencing known Maharashtra/Central acts

  Tier 3 — RAGAS (LLM-judge, requires `ragas` + `datasets` packages):
    - ragas_faithfulness         : claims in output supported by cache context
    - ragas_answer_relevance     : output addresses the fact pattern
    - ragas_context_precision    : fraction of cache actually used
    - ragas_context_recall       : cache contained what was needed

Usage (Tier 1+2, always available):
    from src.evaluation.evaluator import evaluate_cag_document
    result = evaluate_cag_document(doc, cache, fact_pattern, template_slots, latency_s)

Usage (Tier 3, requires ragas):
    from src.evaluation.evaluator import evaluate_ragas
    ragas_scores = evaluate_ragas(doc, cache, fact_pattern)
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known Maharashtra / Central acts for jurisdictional accuracy check
# ---------------------------------------------------------------------------

_MAHARASHTRA_ACTS: frozenset[str] = frozenset({
    "transfer of property act",
    "registration act",
    "maharashtra land revenue code",
    "maharashtra land revenue code, 1966",
    "mlrc",
    "maharashtra rent control act",
    "maharashtra rent control act, 1999",
    "indian stamp act",
    "indian stamp act, 1899",
    "maharashtra stamp act",
    "maharashtra stamp act, 1958",
    "indian contract act",
    "indian evidence act",
    "indian evidence act, 1872",
    "specific relief act",
    "specific relief act, 1963",
    "limitation act",
    "real estate regulation and development act",
    "real estate (regulation and development) act",
    "real estate (regulation and development) act, 2016",
    "rera",
    "maharashtra cooperative societies act",
    "bombay stamp act",
    "bombay tenancy and agricultural lands act",
    "maharashtra ownership flats act",
    "maharashtra ownership flats act, 1963",
    "mofa",
    "powers of attorney act",
    "powers of attorney act, 1882",
    "code of civil procedure",
    "code of civil procedure, 1908",
    "prevention of money laundering act",
    "income tax act",
    "companies act",
    "negotiable instruments act",
})

# Citation format: [Act Name, Year] Section X(Y), Court, Year
# Accepts both bracketed [Court], [Year] and unbracketed Court, Year forms
# since _resolve_citation_placeholders fills in values without brackets.
_CITATION_FORMAT_RE = re.compile(
    r"\[([^\]]+?,\s*\d{4})\]\s+Section\s+[\w\(\)\.\/\-]+,\s*\[?([A-Za-z][^\],\[\n]+?)\]?,\s*\[?\d{4}\]?",
    re.IGNORECASE,
)

# Any citation-like token (to count total citations including malformed ones)
_ANY_CITATION_RE = re.compile(
    r"\[([A-Za-z][^\]]*?,\s*\d{4})\]",
    re.IGNORECASE,
)

# Ungrounded / unavailable markers
_UNGROUNDED_RE = re.compile(
    r"\[(UNGROUNDED|GROQ_UNAVAILABLE|OLLAMA_UNAVAILABLE|AGENT_TIMEOUT)[^\]]*\]",
    re.IGNORECASE,
)

# Per-slot minimum token thresholds for "filled" determination.
# PARTIES and ATTESTATION are structurally compact (names, addresses, signatures)
# so they legitimately produce fewer tokens than OPERATIVE_CLAUSE sections.
# Using a single global threshold causes false "missing" flags for these slots.
_MIN_SLOT_TOKENS_DEFAULT = 50
_MIN_SLOT_TOKENS_BY_TYPE: dict[str, int] = {
    "PARTIES_CLAUSE": 20,       # names + roles + addresses — compact by design
    "ATTESTATION": 15,          # execution date + signature lines — very short
    "RECITALS": 25,             # WHEREAS clauses — can be compact for POA/L&L/affidavit
    "SCHEDULE": 30,             # property description — medium length
    # OPERATIVE_CLAUSE_* slots use the default (50) — substantive legal text
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Tier1Metrics:
    """CAG-specific metrics computable from existing pipeline outputs."""
    cache_hit_rate: float           # 0.0–1.0; grounded / (grounded + ungrounded)
    slot_fill_rate: float           # 0.0–1.0; filled slots / total slots
    fact_fidelity_score: float      # 0.0–1.0; intake fields found in output
    latency_seconds: float          # wall-clock generation time
    grounded_count: int
    ungrounded_count: int
    filled_slots: list[str]
    missing_slots: list[str]
    matched_fields: list[str]
    missing_fields: list[str]


@dataclass
class Tier2Metrics:
    """Structural/legal quality metrics — regex-based, no LLM needed."""
    section_completeness: float         # 0.0–1.0
    citation_format_compliance: float   # 0.0–1.0
    jurisdictional_accuracy: float      # 0.0–1.0
    total_citations: int
    compliant_citations: int
    jurisdictional_citations: int


@dataclass
class CAGEvaluationResult:
    """Full evaluation result for one generated document."""
    run_id: str
    doc_type: str
    pipeline_variant: str
    tier1: Tier1Metrics
    tier2: Tier2Metrics
    ragas: dict[str, float] = field(default_factory=dict)  # populated by evaluate_ragas()

    def summary(self) -> dict[str, Any]:
        """Flat dict suitable for JSON serialisation / CSV export."""
        return {
            "run_id": self.run_id,
            "doc_type": self.doc_type,
            "pipeline_variant": self.pipeline_variant,
            # Tier 1
            "cache_hit_rate": round(self.tier1.cache_hit_rate, 4),
            "slot_fill_rate": round(self.tier1.slot_fill_rate, 4),
            "fact_fidelity_score": round(self.tier1.fact_fidelity_score, 4),
            "latency_seconds": round(self.tier1.latency_seconds, 2),
            "grounded_citations": self.tier1.grounded_count,
            "ungrounded_citations": self.tier1.ungrounded_count,
            # Tier 2
            "section_completeness": round(self.tier2.section_completeness, 4),
            "citation_format_compliance": round(self.tier2.citation_format_compliance, 4),
            "jurisdictional_accuracy": round(self.tier2.jurisdictional_accuracy, 4),
            # Tier 3 (empty until evaluate_ragas() is called)
            **{f"ragas_{k}": round(v, 4) for k, v in self.ragas.items()},
        }


# ---------------------------------------------------------------------------
# Tier 1 — CAG-specific metrics
# ---------------------------------------------------------------------------


def _compute_cache_hit_rate(doc) -> tuple[float, int, int]:
    """
    Compute cache hit rate from a GeneratedDocument.
    Returns (rate, grounded_count, ungrounded_count).
    """
    grounded = len(doc.citations)
    ungrounded = len(doc.ungrounded_clauses)
    total = grounded + ungrounded
    rate = grounded / total if total > 0 else 1.0
    return rate, grounded, ungrounded


def _compute_slot_fill_rate(
    body: str,
    template_slots: list[str],
) -> tuple[float, list[str], list[str]]:
    """
    Compute slot fill rate by checking whether each expected slot heading
    has substantial content (>= _MIN_SLOT_TOKENS tokens) following it.

    Returns (rate, filled_slots, missing_slots).
    """
    if not template_slots:
        return 1.0, [], []

    # Map slot names to the headings _assemble_from_slots() produces
    def _heading(slot: str) -> str:
        if slot == "PARTIES_CLAUSE":
            return "PARTIES"
        if slot == "RECITALS":
            return "RECITALS"
        if slot == "SCHEDULE":
            return "SCHEDULE OF PROPERTY"
        if slot == "ATTESTATION":
            return "ATTESTATION"
        if slot.startswith("OPERATIVE_CLAUSE_"):
            n = slot.split("_")[-1]
            return f"OPERATIVE CLAUSE {n}"
        return slot.replace("_", " ")

    filled: list[str] = []
    missing: list[str] = []

    for slot in template_slots:
        heading = _heading(slot)
        # Find the heading in the body (case-insensitive)
        pattern = re.compile(
            rf"(?:^|\n){re.escape(heading)}\s*\n+(.*?)(?=\n[A-Z]{{3,}}|\Z)",
            re.DOTALL | re.IGNORECASE,
        )
        m = pattern.search(body)
        if m:
            content = m.group(1).strip()
            # Check for ungrounded/unavailable markers that indicate empty content
            is_marker_only = bool(_UNGROUNDED_RE.fullmatch(content.strip()))
            token_count = len(content.split())
            # Use per-slot threshold; fall back to default for OPERATIVE_CLAUSE_* etc.
            min_tokens = _MIN_SLOT_TOKENS_BY_TYPE.get(slot, _MIN_SLOT_TOKENS_DEFAULT)
            if token_count >= min_tokens and not is_marker_only:
                filled.append(slot)
            else:
                missing.append(slot)
        else:
            missing.append(slot)

    rate = len(filled) / len(template_slots) if template_slots else 1.0
    return rate, filled, missing


def _compute_fact_fidelity(body: str, fact_pattern: dict | str) -> tuple[float, list[str], list[str]]:
    """
    Check whether key intake fields appear verbatim in the generated body.

    For dict fact patterns with an "intake" key, checks each non-empty value.
    For string fact patterns, skips (returns 1.0 — not applicable).

    Returns (score, matched_fields, missing_fields).
    """
    if not isinstance(fact_pattern, dict):
        return 1.0, [], []

    intake = fact_pattern.get("intake", fact_pattern)
    if not isinstance(intake, dict):
        return 1.0, [], []

    # Fields to skip — these are structural/metadata, not document content
    _SKIP_FIELDS = {
        "doc_type", "document_type", "pipeline", "backend", "llm_backend",
        "session_id", "run_id",
    }

    # Normalise body: replace Rs. prefix with ₹ so both forms match
    body_normalised = body.lower().replace("rs.", "₹").replace("rs ", "₹")
    body_lower = body.lower()

    matched: list[str] = []
    missing: list[str] = []

    for key, value in intake.items():
        if key in _SKIP_FIELDS:
            continue
        if value in (None, "", [], {}):
            continue
        # Normalise value to string
        val_str = str(value).strip()
        if len(val_str) < 3:
            continue  # too short to be meaningful

        # Normalise value for currency comparison (₹ and Rs. are equivalent)
        val_normalised = val_str.lower().replace("rs.", "₹").replace("rs ", "₹")

        if val_normalised in body_normalised or val_str.lower() in body_lower:
            matched.append(key)
        else:
            # For currency fields, also check if the numeric portion appears
            # (LLM may reformat ₹45,00,000 as Rs.45,00,000 or 45,00,000)
            numeric_part = re.sub(r"[₹,\s(][^)]*\)", "", val_str).strip().rstrip(")")
            numeric_part = re.sub(r"[^\d,]", "", numeric_part)
            if numeric_part and len(numeric_part) >= 3 and numeric_part in body.replace(" ", ""):
                matched.append(key)
            else:
                # Indian lakh/crore normalisation: raw int 3200000 -> "32,00,000"
                # The LLM always formats amounts in Indian style; the intake may
                # supply a plain integer. Convert and check both directions.
                _found = False
                raw_digits = re.sub(r"[^\d]", "", val_str)
                if raw_digits and len(raw_digits) >= 4:
                    try:
                        n = int(raw_digits)
                        # Format as Indian comma-separated (e.g. 3200000 -> 32,00,000)
                        def _indian_fmt(x: int) -> str:
                            s = str(x)
                            if len(s) <= 3:
                                return s
                            # last 3 digits, then groups of 2
                            result = s[-3:]
                            s = s[:-3]
                            while s:
                                result = s[-2:] + "," + result
                                s = s[:-2]
                            return result
                        indian = _indian_fmt(n)
                        if indian in body.replace(" ", ""):
                            _found = True
                        # Also check plain integer string in body
                        if not _found and raw_digits in body.replace(" ", "").replace(",", ""):
                            _found = True
                    except ValueError:
                        pass
                if _found:
                    matched.append(key)
                else:
                    missing.append(key)
                    logger.debug("Fact fidelity: field '%s' value '%s' not found in output.", key, val_str)

    total = len(matched) + len(missing)
    score = len(matched) / total if total > 0 else 1.0
    return score, matched, missing


# ---------------------------------------------------------------------------
# Tier 2 — Structural/legal quality metrics
# ---------------------------------------------------------------------------


def _compute_tier2(body: str) -> Tier2Metrics:
    """
    Compute Tier 2 metrics from the document body text.
    All checks are regex-based — no LLM required.
    """
    # --- Section completeness ---
    # Count expected section headings that have content
    expected_headings = [
        "PARTIES", "RECITALS", "OPERATIVE CLAUSE", "SCHEDULE", "ATTESTATION",
    ]
    present = sum(
        1 for h in expected_headings
        if re.search(rf"(?:^|\n){re.escape(h)}", body, re.IGNORECASE)
    )
    section_completeness = present / len(expected_headings)

    # --- Citation format compliance ---
    # Count all citation-like tokens vs those matching the standard format
    all_citations = _ANY_CITATION_RE.findall(body)
    compliant = _CITATION_FORMAT_RE.findall(body)
    total_cit = len(all_citations)
    compliant_count = len(compliant)
    citation_format_compliance = compliant_count / total_cit if total_cit > 0 else 1.0

    # --- Jurisdictional accuracy ---
    # Check cited act names against known Maharashtra/Central acts
    jurisdictional_count = 0
    for act_year in all_citations:
        act_name = act_year.rsplit(",", 1)[0].lower().strip()
        if act_name.startswith("the "):
            act_name = act_name[4:]
        if any(known in act_name for known in _MAHARASHTRA_ACTS):
            jurisdictional_count += 1

    jurisdictional_accuracy = jurisdictional_count / total_cit if total_cit > 0 else 1.0

    return Tier2Metrics(
        section_completeness=section_completeness,
        citation_format_compliance=citation_format_compliance,
        jurisdictional_accuracy=jurisdictional_accuracy,
        total_citations=total_cit,
        compliant_citations=compliant_count,
        jurisdictional_citations=jurisdictional_count,
    )


# ---------------------------------------------------------------------------
# Public API — Tier 1 + 2
# ---------------------------------------------------------------------------


def evaluate_cag_document(
    doc,                        # GeneratedDocument
    fact_pattern: dict | str,
    template_slots: list[str] | None,
    latency_seconds: float,
) -> CAGEvaluationResult:
    """
    Compute Tier 1 and Tier 2 metrics for a generated document.

    Args:
        doc:              GeneratedDocument returned by generate_document().
        fact_pattern:     The intake fact pattern dict (or string) used for generation.
        template_slots:   List of slot names from the template (e.g. ["PARTIES_CLAUSE", ...]).
        latency_seconds:  Wall-clock time from generate_draft() call to DraftResult.

    Returns:
        CAGEvaluationResult with tier1 and tier2 populated.
        Call evaluate_ragas() separately to populate tier3.
    """
    # Tier 1
    cache_hit_rate, grounded, ungrounded = _compute_cache_hit_rate(doc)
    slot_fill_rate, filled_slots, missing_slots = _compute_slot_fill_rate(
        doc.body, template_slots or []
    )
    fact_fidelity, matched_fields, missing_fields = _compute_fact_fidelity(
        doc.body, fact_pattern
    )

    tier1 = Tier1Metrics(
        cache_hit_rate=cache_hit_rate,
        slot_fill_rate=slot_fill_rate,
        fact_fidelity_score=fact_fidelity,
        latency_seconds=latency_seconds,
        grounded_count=grounded,
        ungrounded_count=ungrounded,
        filled_slots=filled_slots,
        missing_slots=missing_slots,
        matched_fields=matched_fields,
        missing_fields=missing_fields,
    )

    # Tier 2
    tier2 = _compute_tier2(doc.body)

    return CAGEvaluationResult(
        run_id=doc.run_id,
        doc_type=getattr(doc, "doc_type", "unknown"),
        pipeline_variant=getattr(doc, "pipeline_variant", "CAG"),
        tier1=tier1,
        tier2=tier2,
    )


# ---------------------------------------------------------------------------
# Public API — Tier 3 (RAGAS)
# ---------------------------------------------------------------------------


def _build_ragas_llm_and_embeddings():
    """
    Build the RAGAS 0.4.x LLM judge and embedding model.

    LLM  : Groq llama-3.1-8b-instant via OpenAI-compatible endpoint.
           Uses GROQ_RAGAS_API_KEY if set, falls back to GROQ_API_KEY.
    Emb  : all-MiniLM-L6-v2 via sentence-transformers (local, no API key needed).

    Returns (llm, embeddings) or raises ImportError / ValueError.
    """
    import os
    from openai import OpenAI
    from ragas.llms import llm_factory
    from ragas.embeddings.base import LangchainEmbeddingsWrapper

    # Suppress the LangChain deprecation warning for the old HuggingFaceEmbeddings
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from langchain_community.embeddings import HuggingFaceEmbeddings

    api_key = os.getenv("GROQ_RAGAS_API_KEY") or os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "No Groq API key found for RAGAS. "
            "Set GROQ_RAGAS_API_KEY or GROQ_API_KEY in .env"
        )

    client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
    # llama-3.3-70b-versatile: 32k output tokens — needed for RAGAS faithfulness
    # which generates verbose statement-by-statement JSON that exceeds 8b's 3k limit.
    llm = llm_factory("llama-3.3-70b-versatile", provider="openai", client=client)
    embeddings = LangchainEmbeddingsWrapper(
        HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    )
    return llm, embeddings


def evaluate_ragas(
    doc,                    # GeneratedDocument
    cache,                  # LegalCache
    fact_pattern: dict | str,
) -> dict[str, float]:
    """
    Compute RAGAS Tier 3 metrics for a generated document.

    Uses ragas 0.4.x API:
      - LLM judge : Groq llama-3.1-8b-instant (GROQ_RAGAS_API_KEY or GROQ_API_KEY)
      - Embeddings: all-MiniLM-L6-v2 via sentence-transformers (local, free)

    CAG → RAGAS field mapping (0.4.x SingleTurnSample schema):
      user_input         ← serialised fact pattern (the "question")
      response           ← generated document body (the "answer")
      retrieved_contexts ← loaded cache document texts
      reference          ← serialised fact pattern (ground truth for relevance)

    Metrics computed:
      faithfulness                      — claims grounded in cache context
      answer_relevancy                  — output addresses the fact pattern
      llm_context_precision_with_reference — fraction of cache actually used
      context_recall                    — cache contained what was needed

    Returns a flat dict with normalised keys. Returns {} on any failure
    (non-fatal — Tier 1/2 results are unaffected).
    """
    try:
        from ragas import evaluate as ragas_evaluate
        from ragas.metrics import (
            _Faithfulness,
            _AnswerRelevancy,
            _LLMContextPrecisionWithReference,
            _LLMContextRecall,
        )
        from ragas.dataset_schema import SingleTurnSample, EvaluationDataset
    except ImportError:
        logger.warning(
            "ragas not installed — skipping Tier 3. "
            "Install with: pip install ragas"
        )
        return {}

    # --- Serialise fact pattern → user_input / reference ---
    if isinstance(fact_pattern, dict):
        intake = fact_pattern.get("intake", fact_pattern)
        question = " ".join(
            f"{k}: {v}" for k, v in intake.items()
            if v not in (None, "", [])
        )
    else:
        question = str(fact_pattern)

    # --- Build context list from loaded cache documents ---
    contexts = [e.content for e in cache.documents if e.content]
    if not contexts:
        logger.warning("No cache content available for RAGAS context evaluation.")
        return {}

    # --- Truncate to stay within Groq's context window ---
    # RAGAS faithfulness sends: system prompt + all contexts + full document body.
    # llama-3.1-8b-instant has an 8k output limit; the faithfulness prompt itself
    # uses ~2k tokens for instructions, leaving ~4k for content.
    # We truncate each context to 600 chars and the document body to 1500 chars.
    _MAX_CTX_CHARS  = 600
    _MAX_BODY_CHARS = 1500
    contexts  = [c[:_MAX_CTX_CHARS] for c in contexts]
    body_text = doc.body[:_MAX_BODY_CHARS]

    try:
        llm, embeddings = _build_ragas_llm_and_embeddings()
    except Exception as exc:
        logger.error("RAGAS LLM/embedding setup failed: %s", exc)
        return {}

    # --- Build EvaluationDataset (ragas 0.4.x schema) ---
    sample = SingleTurnSample(
        user_input=question,
        response=body_text,
        retrieved_contexts=contexts,
        reference=question,
    )
    dataset = EvaluationDataset(samples=[sample])

    # --- Instantiate metrics with the Groq judge ---
    metrics = [
        _Faithfulness(llm=llm),
        _AnswerRelevancy(llm=llm, embeddings=embeddings),
        _LLMContextPrecisionWithReference(llm=llm),
        _LLMContextRecall(llm=llm),
    ]

    try:
        result = ragas_evaluate(
            dataset=dataset,
            metrics=metrics,
            llm=llm,
            embeddings=embeddings,
            show_progress=False,
            raise_exceptions=False,
        )
        row = result.to_pandas().iloc[0].to_dict()
        return {
            # Normalise to consistent keys regardless of ragas internal naming
            "faithfulness":        float(row.get("faithfulness", 0.0)),
            "answer_relevance":    float(row.get("answer_relevancy", 0.0)),
            "context_precision":   float(row.get("llm_context_precision_with_reference", 0.0)),
            "context_recall":      float(row.get("context_recall", 0.0)),
        }
    except Exception as exc:
        logger.error("RAGAS evaluate() failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Answer Degradation vs Fresh Retrieval
# ---------------------------------------------------------------------------


def compute_answer_degradation(
    cag_result: CAGEvaluationResult,
    fresh_result: CAGEvaluationResult,
) -> dict[str, float]:
    """
    Compare CAG (fixed cache) vs fresh retrieval (Dense/Hybrid RAG) quality.

    A positive degradation value means CAG performed worse than fresh retrieval.
    A negative value means CAG performed better (cache is high quality).

    Returns a dict of metric deltas: {metric: cag_value - fresh_value}.
    """
    metrics = [
        "cache_hit_rate", "slot_fill_rate", "fact_fidelity_score",
        "section_completeness", "citation_format_compliance", "jurisdictional_accuracy",
    ]
    cag_summary = cag_result.summary()
    fresh_summary = fresh_result.summary()

    degradation: dict[str, float] = {}
    for m in metrics:
        cag_val = cag_summary.get(m, 0.0)
        fresh_val = fresh_summary.get(m, 0.0)
        # Positive = CAG worse, Negative = CAG better
        degradation[f"{m}_delta"] = round(cag_val - fresh_val, 4)

    # RAGAS deltas if available
    for k in ["faithfulness", "answer_relevance", "context_precision", "context_recall"]:
        cag_val = cag_result.ragas.get(k, None)
        fresh_val = fresh_result.ragas.get(k, None)
        if cag_val is not None and fresh_val is not None:
            degradation[f"ragas_{k}_delta"] = round(cag_val - fresh_val, 4)

    return degradation
