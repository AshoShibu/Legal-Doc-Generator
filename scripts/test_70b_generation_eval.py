#!/usr/bin/env python3
"""
scripts/test_70b_generation_eval.py

Methodical test: generate a Sale Deed using Groq llama-3.3-70b-versatile
via the full CAG pipeline, then run Tier 1 + Tier 2 + Tier 3 evaluation.

Tier 3 is implemented directly via the Groq API (no ragas package needed)
to avoid Python 3.14 / Pydantic v1 compatibility issues.

Usage:
    python scripts/test_70b_generation_eval.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import requests

SEP  = "=" * 70
THIN = "-" * 70

# ── Fact pattern ─────────────────────────────────────────────────────────────
FACT_PATTERN = {
    "document_type": "sale_deed",
    "ocr_fields": {
        "Survey_Number": "5464489",
        "owner_name": "<PETITIONER_1>",
        "area": "5000 sq. metres",
    },
    "intake": {
        "seller_name": "<PETITIONER_1>",
        "seller_address": "Kiran Samruddhi B, Sus Gaon, Pune, Maharashtra",
        "buyer_name": "<RESPONDENT_1>",
        "buyer_address": "Balaji Whitefield, Sus Gaon, Pune, Maharashtra",
        "survey_number": "5464489",
        "area_sqm": "5000 sq. metres",
        "village": "Sus Gaon",
        "taluka": "Pirangut",
        "district": "Pune",
        "encumbrance": True,
        "consideration_amount": "₹10,00,000 (Rupees Ten Lakhs only)",
        "payment_mode": "RTGS/NEFT",
        "advance_paid": "₹1,00,000 (Rupees One Lakh only)",
        "execution_date": "23 March 2026",
        "registration_office": "Baner Registrar Office, Pune",
        "witness_1_name": "<WITNESS_1>",
        "witness_2_name": "<WITNESS_2>",
    },
}

TEMPLATE_SLOTS = [
    "PARTIES_CLAUSE",
    "RECITALS",
    "OPERATIVE_CLAUSE_1",
    "OPERATIVE_CLAUSE_2",
    "OPERATIVE_CLAUSE_3",
    "SCHEDULE",
    "ATTESTATION",
]

LLM_BACKEND = "groq_llama3_70b"
DOC_TYPE     = "sale_deed"
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"
JUDGE_MODEL  = "llama-3.3-70b-versatile"


# ── Tier 3: direct Groq faithfulness judge ───────────────────────────────────

def _groq_call(api_key: str, model: str, prompt: str, max_tokens: int = 512) -> str:
    for attempt in range(4):
        resp = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": max_tokens,
            },
            timeout=(10, 60),
        )
        if resp.status_code == 429:
            wait = float(resp.headers.get("Retry-After", 4 * (2 ** attempt)))
            print(f"      [rate limit — waiting {wait:.0f}s before retry {attempt+1}/3]", flush=True)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    raise RuntimeError("Groq 429 — all retries exhausted")


def evaluate_faithfulness(body: str, cache_context: str, api_key: str) -> float:
    """
    Ask the 70b judge: for each claim in the document body, is it supported
    by the cache context? Returns fraction of supported claims (0.0–1.0).
    """
    # Extract sentences that look like legal claims (contain a verb + legal term)
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', body) if len(s.split()) > 6]
    sentences = sentences[:12]  # cap at 12 to stay within token budget

    if not sentences:
        return 1.0

    claims_block = "\n".join(f"{i+1}. {s}" for i, s in enumerate(sentences))
    prompt = f"""You are a legal document evaluator.

CONTEXT (loaded legal statutes):
{cache_context[:1500]}

CLAIMS from the generated document:
{claims_block}

For each claim, answer YES if it is supported by the CONTEXT above, or NO if it is not.
Reply with ONLY a JSON array of booleans in order, e.g.: [true, false, true, ...]
"""
    try:
        raw = _groq_call(api_key, JUDGE_MODEL, prompt, max_tokens=200)
        # Extract JSON array from response
        match = re.search(r'\[[\s\w,]+\]', raw)
        if match:
            verdicts = json.loads(match.group())
            supported = sum(1 for v in verdicts if v)
            return round(supported / len(verdicts), 4) if verdicts else 1.0
    except Exception as e:
        print(f"      [faithfulness judge error: {e}]")
    return 0.0


def evaluate_answer_relevance(body: str, fact_pattern: dict, api_key: str) -> float:
    """
    Ask the judge: does the document address the fact pattern (question)?
    Returns a score 0–10 normalised to 0.0–1.0.
    """
    intake = fact_pattern.get("intake", {})
    question = ", ".join(f"{k}: {v}" for k, v in list(intake.items())[:8])
    prompt = f"""You are a legal document evaluator.

QUESTION (fact pattern for a Sale Deed):
{question}

GENERATED DOCUMENT (first 800 chars):
{body[:800]}

On a scale of 0 to 10, how well does the generated document address the question?
Reply with ONLY a single integer between 0 and 10.
"""
    try:
        raw = _groq_call(api_key, JUDGE_MODEL, prompt, max_tokens=10)
        score = int(re.search(r'\d+', raw).group())
        return round(min(score, 10) / 10.0, 4)
    except Exception as e:
        print(f"      [answer relevance judge error: {e}]")
    return 0.0


def evaluate_context_precision(body: str, cache_docs: list, api_key: str) -> float:
    """
    What fraction of the loaded cache documents were actually cited in the output?
    """
    if not cache_docs:
        return 1.0
    cited = 0
    for doc in cache_docs:
        # Check if any keyword from the doc name appears in the body
        keywords = [w for w in doc.name.split() if len(w) > 4 and w.isalpha()]
        if any(kw.lower() in body.lower() for kw in keywords):
            cited += 1
    return round(cited / len(cache_docs), 4)


def evaluate_context_recall(body: str, cache_docs: list, fact_pattern: dict, api_key: str) -> float:
    """
    Ask the judge: did the cache contain the statutes needed to answer the fact pattern?
    """
    cache_names = [d.name for d in cache_docs]
    intake = fact_pattern.get("intake", {})
    question = ", ".join(f"{k}: {v}" for k, v in list(intake.items())[:6])
    prompt = f"""You are a legal document evaluator.

TASK: A Sale Deed needs to be drafted for this fact pattern:
{question}

AVAILABLE STATUTES in the cache:
{chr(10).join(f'- {n}' for n in cache_names)}

On a scale of 0 to 10, how well does the available cache cover the statutes needed for this Sale Deed?
Reply with ONLY a single integer between 0 and 10.
"""
    try:
        raw = _groq_call(api_key, JUDGE_MODEL, prompt, max_tokens=10)
        score = int(re.search(r'\d+', raw).group())
        return round(min(score, 10) / 10.0, 4)
    except Exception as e:
        print(f"      [context recall judge error: {e}]")
    return 0.0


# ── Main ──────────────────────────────────────────────────────────────────────

def run_tier3_only() -> None:
    """Re-run only Tier 3 on the previously generated document."""
    api_key  = os.environ.get("GROQ_API_KEY", "")
    ragas_key = os.environ.get("GROQ_RAGAS_API_KEY") or api_key

    prev = ROOT / "output" / "70b_eval_result.json"
    if not prev.exists():
        print("No previous result found. Run without --eval-only first.")
        sys.exit(1)

    data = json.loads(prev.read_text(encoding="utf-8"))
    docx_path = data.get("docx_path")
    if not docx_path or not Path(docx_path).exists():
        print(f"DOCX not found at {docx_path}"); sys.exit(1)

    # Re-extract body from DOCX
    try:
        from docx import Document as DocxDoc
        doc = DocxDoc(docx_path)
        body = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as e:
        print(f"Could not read DOCX: {e}"); sys.exit(1)

    # Reload cache for context
    from src.cag.engine import load_cache
    from src.cag.engine import _build_cache_context
    cache = load_cache(DOC_TYPE, LLM_BACKEND)
    cache_context = _build_cache_context(cache, max_chars=3000)

    print(SEP)
    print("  Tier 3 Re-evaluation (with rate-limit backoff)")
    print(SEP)
    print(f"  Document : {Path(docx_path).name}")
    print(f"  Body     : {len(body):,} chars")
    print()

    print("  Running faithfulness judge ...", flush=True)
    faithfulness = evaluate_faithfulness(body, cache_context, ragas_key)
    print(f"  faithfulness      : {faithfulness:.4f}")

    time.sleep(3)
    print("  Running answer relevance judge ...", flush=True)
    answer_relevance = evaluate_answer_relevance(body, FACT_PATTERN, ragas_key)
    print(f"  answer_relevance  : {answer_relevance:.4f}")

    time.sleep(3)
    print("  Running context precision ...", flush=True)
    context_precision = evaluate_context_precision(body, cache.documents, ragas_key)
    print(f"  context_precision : {context_precision:.4f}")

    time.sleep(3)
    print("  Running context recall judge ...", flush=True)
    context_recall = evaluate_context_recall(body, cache.documents, FACT_PATTERN, ragas_key)
    print(f"  context_recall    : {context_recall:.4f}")

    data.update({
        "tier3_faithfulness": faithfulness,
        "tier3_answer_relevance": answer_relevance,
        "tier3_context_precision": context_precision,
        "tier3_context_recall": context_recall,
    })
    prev.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Updated → output/70b_eval_result.json")
    print(SEP)


def main() -> None:
    print(SEP)
    print("  Maharashtra Legal Document — 70B Generation + Full Evaluation")
    print(SEP)
    print(f"  Model   : llama-3.3-70b-versatile (Groq)")
    print(f"  DocType : {DOC_TYPE}  |  Slots: {len(TEMPLATE_SLOTS)}")
    print(SEP)

    api_key = os.environ.get("GROQ_API_KEY", "")
    ragas_key = os.environ.get("GROQ_RAGAS_API_KEY") or api_key
    if not api_key:
        print("\nERROR: GROQ_API_KEY not set in .env"); sys.exit(1)
    print(f"  GROQ_API_KEY      : {api_key[:8]}...{api_key[-4:]}  ✓")
    print(f"  GROQ_RAGAS_API_KEY: {ragas_key[:8]}...{ragas_key[-4:]}  ✓")
    print()

    # ── 1. Load cache ─────────────────────────────────────────────────────────
    print("[1/4] Loading legal cache ...")
    from src.cag.engine import load_cache, generate_draft, LegalCache, DraftResult
    from src.cag.engine import _build_cache_context

    t0 = time.time()
    cache: LegalCache = load_cache(DOC_TYPE, LLM_BACKEND)
    cache_load_time = time.time() - t0

    print(f"      Documents : {len(cache.documents)}")
    print(f"      Tokens    : {cache.total_tokens:,}")
    print(f"      Omitted   : {cache.omitted_documents or 'none'}")
    print(f"      Time      : {cache_load_time:.2f}s")
    for d in cache.documents:
        print(f"        • {d.name}  ({d.tokens} tok)")
    print()

    # ── 2. Generate draft ─────────────────────────────────────────────────────
    print(f"[2/4] Generating draft ({len(TEMPLATE_SLOTS)} slots, two-pass, 70b) ...")
    print("      ~30-45s expected (2s inter-slot delay × 7 slots)\n")

    def slot_cb(idx: int, name: str) -> None:
        print(f"      [{idx+1}/{len(TEMPLATE_SLOTS)}] {name}", flush=True)

    t1 = time.time()
    draft: DraftResult = generate_draft(
        fact_pattern=FACT_PATTERN,
        cache=cache,
        doc_type=DOC_TYPE,
        template_slots=TEMPLATE_SLOTS,
        slot_progress_cb=slot_cb,
    )
    gen_time = time.time() - t1

    print(f"\n      Time      : {gen_time:.1f}s")
    print(f"      Length    : {len(draft.content):,} chars")
    print(f"      Citations : {len(draft.citations)}")
    print(f"      Ungrounded: {len(draft.ungrounded_clauses)}")
    print()

    # ── 3. Assemble + export ──────────────────────────────────────────────────
    print("[3/4] Assembling document + exporting DOCX/PDF ...")
    from src.generation.document_generator import generate_document, GeneratedDocument
    from src.generation.exporter import export_document

    run_id = str(uuid.uuid4())
    generated: GeneratedDocument = generate_document(
        llm_output=draft.content,
        template=None,
        cache_or_chunks=cache,
        doc_type=DOC_TYPE,
        pipeline_variant="CAG",
        llm_model="groq_llama3_70b",
        run_id=run_id,
        cache_version="1.0.0",
    )
    out_dir = ROOT / "output" / "cag" / DOC_TYPE / run_id
    export_result = export_document(generated, str(out_dir), run_id)

    print(f"      Run ID : {run_id[:12]}...")
    print(f"      DOCX   : {Path(export_result.docx_path).name if export_result.docx_path else 'failed'}")
    print(f"      PDF    : {Path(export_result.pdf_path).name if export_result.pdf_path else 'failed'}")
    print()

    # ── 4. Evaluation ─────────────────────────────────────────────────────────
    print("[4/4] Running evaluation ...")
    from src.evaluation.evaluator import evaluate_cag_document

    # Tier 1 + 2
    eval_result = evaluate_cag_document(
        doc=generated,
        fact_pattern=FACT_PATTERN,
        template_slots=TEMPLATE_SLOTS,
        latency_seconds=gen_time,
    )
    t1m = eval_result.tier1
    t2m = eval_result.tier2
    print("      Tier 1+2 complete.")

    # Tier 3 — direct Groq judge (no ragas package)
    print("      Running Tier 3 (direct Groq 70b judge) ...")
    cache_context = _build_cache_context(cache, max_chars=3000)
    t3_start = time.time()

    faithfulness      = evaluate_faithfulness(generated.body, cache_context, ragas_key)
    answer_relevance  = evaluate_answer_relevance(generated.body, FACT_PATTERN, ragas_key)
    context_precision = evaluate_context_precision(generated.body, cache.documents, ragas_key)
    context_recall    = evaluate_context_recall(generated.body, cache.documents, FACT_PATTERN, ragas_key)

    t3_time = time.time() - t3_start
    print(f"      Tier 3 complete ({t3_time:.1f}s).")
    print()

    # ── Report ────────────────────────────────────────────────────────────────
    print(SEP)
    print("  EVALUATION RESULTS  —  llama-3.3-70b-versatile / Sale Deed / CAG")
    print(SEP)

    print()
    print("  ── Tier 1: CAG-Specific Metrics ──────────────────────────────────")
    print(f"  cache_hit_rate       : {t1m.cache_hit_rate:.4f}"
          f"  (grounded={t1m.grounded_count}, ungrounded={t1m.ungrounded_count})")
    print(f"  slot_fill_rate       : {t1m.slot_fill_rate:.4f}"
          f"  ({len(t1m.filled_slots)}/{len(TEMPLATE_SLOTS)} slots filled)")
    print(f"  fact_fidelity_score  : {t1m.fact_fidelity_score:.4f}"
          f"  (matched={len(t1m.matched_fields)}, missing={len(t1m.missing_fields)})")
    print(f"  latency_seconds      : {t1m.latency_seconds:.1f}s")
    if t1m.filled_slots:
        print(f"  filled_slots         : {', '.join(t1m.filled_slots)}")
    if t1m.missing_slots:
        print(f"  missing_slots        : {', '.join(t1m.missing_slots)}")
    if t1m.matched_fields:
        print(f"  matched_fields       : {', '.join(t1m.matched_fields)}")
    if t1m.missing_fields:
        print(f"  missing_fields       : {', '.join(t1m.missing_fields)}")

    print()
    print("  ── Tier 2: Structural / Legal Quality ────────────────────────────")
    print(f"  section_completeness : {t2m.section_completeness:.4f}"
          f"  (sections present / 5 expected)")
    print(f"  citation_fmt_comply  : {t2m.citation_format_compliance:.4f}"
          f"  ({t2m.compliant_citations}/{t2m.total_citations} well-formed)")
    print(f"  jurisdictional_acc   : {t2m.jurisdictional_accuracy:.4f}"
          f"  ({t2m.jurisdictional_citations}/{t2m.total_citations} Maharashtra/Central)")

    print()
    print("  ── Tier 3: LLM-Judge (Groq llama-3.3-70b-versatile) ─────────────")
    print(f"  faithfulness         : {faithfulness:.4f}"
          f"  (claims grounded in cache)")
    print(f"  answer_relevance     : {answer_relevance:.4f}"
          f"  (output addresses fact pattern)")
    print(f"  context_precision    : {context_precision:.4f}"
          f"  (cache docs actually cited)")
    print(f"  context_recall       : {context_recall:.4f}"
          f"  (cache covered needed statutes)")

    print()
    print("  ── Document Preview ──────────────────────────────────────────────")
    print(THIN)
    for line in generated.body[:700].strip().splitlines():
        print(f"  {line}")
    print(f"  ... [{len(generated.body):,} chars total]")
    print(THIN)

    # ── Save JSON ─────────────────────────────────────────────────────────────
    summary = eval_result.summary()
    summary.update({
        "model": "llama-3.3-70b-versatile",
        "generation_time_s": round(gen_time, 2),
        "cache_load_time_s": round(cache_load_time, 2),
        "docx_path": export_result.docx_path,
        "pdf_path": export_result.pdf_path,
        "tier3_faithfulness": faithfulness,
        "tier3_answer_relevance": answer_relevance,
        "tier3_context_precision": context_precision,
        "tier3_context_recall": context_recall,
    })
    out_json = ROOT / "output" / "70b_eval_result.json"
    out_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print(f"  Results saved → output/70b_eval_result.json")
    print(SEP)


if __name__ == "__main__":
    if "--eval-only" in sys.argv:
        run_tier3_only()
    else:
        main()
