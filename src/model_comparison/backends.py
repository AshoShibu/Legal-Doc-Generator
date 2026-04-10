"""
src/model_comparison/backends.py

Groq and Ollama backend implementations for the model comparison pipeline.

Each backend exposes a single ``generate()`` method that accepts a fact
pattern string, a :class:`~src.model_comparison.model_registry.ModelConfig`,
and a :class:`~src.model_comparison.model_registry.ParameterPreset`, and
returns a ``(document_text, latency_seconds, token_count)`` tuple.

Error handling contract
-----------------------
- HTTP 429 (rate limit)  → exponential backoff: 2 s → 4 s → 8 s, max 3 retries
- Timeout (> 60 s)       → raise :exc:`ModelTimeoutError`
- HTTP 5xx / network err → raise :exc:`ModelAPIError`
"""
from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from src.model_comparison.model_registry import ModelConfig, ParameterPreset

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class ModelTimeoutError(Exception):
    """Raised when a model API call exceeds the 60-second timeout."""


class ModelAPIError(Exception):
    """Raised on HTTP 5xx responses or unrecoverable network errors."""


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_GROQ_TIMEOUT_SECONDS = 60
_OLLAMA_TIMEOUT_SECONDS = 180   # cloud Ollama models can take 2-3 min for long docs
_MAX_RETRIES = 8                # enough to cycle all keys + wait for quota refresh
_BACKOFF_SECONDS = [2, 4, 8, 15, 30, 60, 90, 120]
_GROQ_INTER_REQUEST_DELAY = 2.0  # seconds between Groq calls to stay under RPM
_GROQ_ALL_KEYS_WAIT = 30.0       # minimum wait when all keys are rate-limited

# System prompt — instructs the model to produce a complete legal document.
# Explicit structure guidance helps smaller models (4B) produce well-formed output.
_SYSTEM_PROMPT = (
    "You are an expert legal document drafter specialising in Maharashtra law. "
    "Given a fact pattern, produce a COMPLETE, well-structured legal document "
    "that strictly conforms to Maharashtra legal drafting conventions.\n\n"
    "MANDATORY STRUCTURE — include ALL of the following sections in order:\n"
    "1. Document title and execution date\n"
    "2. PARTIES section — full names, addresses, and roles of all parties\n"
    "3. RECITALS / WHEREAS clauses — background and context\n"
    "4. OPERATIVE CLAUSE — beginning with 'NOW THIS DEED WITNESSETH' or 'IT IS HEREBY AGREED'\n"
    "5. Numbered clauses covering all material terms\n"
    "6. SCHEDULE — property or subject matter description\n"
    "7. ATTESTATION / IN WITNESS WHEREOF — signature blocks with witness lines\n\n"
    "RULES:\n"
    "- Use formal legal language throughout\n"
    "- Reference relevant Maharashtra and Central Acts where applicable\n"
    "- Do NOT truncate or summarise — write the complete document\n"
    "- Output ONLY the document text, no commentary or preamble"
)


def _build_user_prompt(fact_pattern: str) -> str:
    return (
        "Draft a complete Maharashtra legal document based on the following fact pattern. "
        "Follow the mandatory structure exactly — include all 7 sections.\n\n"
        f"FACT PATTERN:\n{fact_pattern}\n\n"
        "Begin the document now:"
    )


def _load_groq_api_keys() -> list[str]:
    """Load all Groq API keys from environment variables.

    Reads GROQ_API_KEY, GROQ_API_KEY_2, GROQ_API_KEY_3, ... up to _N=10.
    Returns a deduplicated list of non-empty keys in order.
    Add multiple keys to .env to enable automatic rotation on 429 rate limits.
    """
    keys: list[str] = []
    seen: set[str] = set()
    candidates = ["GROQ_API_KEY"] + [f"GROQ_API_KEY_{i}" for i in range(2, 11)]
    for var in candidates:
        val = os.environ.get(var, "").strip()
        if val and val not in seen:
            keys.append(val)
            seen.add(val)
    if len(keys) > 1:
        logger.info("Groq key pool: %d keys loaded (%s)", len(keys),
                    ", ".join(v for v in candidates[:len(keys)]))
    return keys


# ---------------------------------------------------------------------------
# Groq backend
# ---------------------------------------------------------------------------

class Groq_Backend:
    """Calls the Groq OpenAI-compatible chat completions API."""

    def __init__(self, config: ModelConfig) -> None:
        self.config = config

    def generate(
        self,
        fact_pattern: str,
        model_config: ModelConfig,
        preset: ParameterPreset,
    ) -> tuple[str, float, int]:
        """Generate a document via the Groq API.

        Args:
            fact_pattern: Plain-text description of the legal transaction.
            model_config: Model configuration (endpoint, model_id, …).
            preset: Sampling parameters (temperature, top_p, max_tokens).

        Returns:
            ``(document_text, latency_seconds, token_count)``

        Raises:
            ModelTimeoutError: Request exceeded 60 seconds.
            ModelAPIError: HTTP 5xx or unrecoverable network error.
        """
        # Build key pool from GROQ_API_KEY, GROQ_API_KEY_2, GROQ_API_KEY_3, ...
        api_keys = _load_groq_api_keys()
        if not api_keys:
            raise ModelAPIError(
                "No Groq API keys found. Set GROQ_API_KEY (and optionally "
                "GROQ_API_KEY_2, GROQ_API_KEY_3, ...) in your .env file."
            )

        url = f"{model_config.api_endpoint}/chat/completions"
        payload = {
            "model": model_config.model_id,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(fact_pattern)},
            ],
            "temperature": preset.temperature,
            "top_p": preset.top_p,
            "max_tokens": preset.max_tokens,
        }

        key_index = 0  # start with first key
        keys_tried_this_round: set[int] = set()

        for attempt in range(_MAX_RETRIES):
            current_key = api_keys[key_index % len(api_keys)]
            headers = {
                "Authorization": f"Bearer {current_key}",
                "Content-Type": "application/json",
            }
            t0 = time.monotonic()
            try:
                resp = requests.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=_GROQ_TIMEOUT_SECONDS,
                )
            except requests.Timeout:
                raise ModelTimeoutError(
                    f"Groq API timed out after {_GROQ_TIMEOUT_SECONDS}s "
                    f"(model={model_config.model_id})"
                )
            except requests.RequestException as exc:
                raise ModelAPIError(
                    f"Groq network error for model={model_config.model_id}: {exc}"
                ) from exc

            latency = time.monotonic() - t0

            if resp.status_code == 429:
                if attempt >= _MAX_RETRIES - 1:
                    raise ModelAPIError(
                        f"Groq rate limit (HTTP 429) exhausted after "
                        f"{_MAX_RETRIES} retries for model={model_config.model_id}."
                    )

                keys_tried_this_round.add(key_index % len(api_keys))
                next_key_index = (key_index + 1) % len(api_keys)

                if len(keys_tried_this_round) < len(api_keys):
                    # Still have untried keys — rotate immediately
                    logger.warning(
                        "Groq 429 on key #%d (model=%s) — rotating to key #%d.",
                        key_index + 1, model_config.model_id, next_key_index + 1,
                    )
                    key_index = next_key_index
                    time.sleep(2)
                else:
                    # All keys exhausted — wait for quota to refresh then retry from key 1
                    retry_after = resp.headers.get("Retry-After")
                    # Cap the wait: use Retry-After but floor at _GROQ_ALL_KEYS_WAIT
                    # and cap at 120s to avoid hanging forever
                    if retry_after:
                        wait = min(float(retry_after), 120.0)
                    else:
                        wait = _GROQ_ALL_KEYS_WAIT
                    logger.warning(
                        "Groq 429: all %d keys rate-limited (model=%s). "
                        "Waiting %.1fs for quota refresh then retrying.",
                        len(api_keys), model_config.model_id, wait,
                    )
                    time.sleep(wait)
                    key_index = 0  # restart from first key after wait
                    keys_tried_this_round.clear()
                continue

            if resp.status_code >= 500:
                raise ModelAPIError(
                    f"Groq HTTP {resp.status_code} for model={model_config.model_id}: "
                    f"{resp.text[:200]}"
                )

            resp.raise_for_status()

            data = resp.json()
            document_text: str = data["choices"][0]["message"]["content"]
            token_count: int = data.get("usage", {}).get("total_tokens", 0)

            logger.debug(
                "Groq generate OK: model=%s preset=%s key=#%d latency=%.2fs tokens=%d",
                model_config.model_id, preset.name, key_index + 1, latency, token_count,
            )
            # Small delay to stay under Groq RPM limits
            time.sleep(_GROQ_INTER_REQUEST_DELAY)
            return document_text, latency, token_count

        # Should never reach here
        raise ModelAPIError(
            f"Groq generate: unexpected retry loop exit for model={model_config.model_id}"
        )


# ---------------------------------------------------------------------------
# Ollama backend
# ---------------------------------------------------------------------------

class Ollama_Backend:
    """Calls the local Ollama HTTP API at http://localhost:11434."""

    def __init__(self, config: ModelConfig) -> None:
        self.config = config

    def generate(
        self,
        fact_pattern: str,
        model_config: ModelConfig,
        preset: ParameterPreset,
    ) -> tuple[str, float, int]:
        """Generate a document via the Ollama local API.

        Args:
            fact_pattern: Plain-text description of the legal transaction.
            model_config: Model configuration (endpoint, model_id, …).
            preset: Sampling parameters (temperature, top_p, max_tokens).

        Returns:
            ``(document_text, latency_seconds, token_count)``

        Raises:
            ModelTimeoutError: Request exceeded 60 seconds.
            ModelAPIError: HTTP 5xx or unrecoverable network error.
        """
        url = f"{model_config.api_endpoint}/api/chat"
        payload = {
            "model": model_config.model_id,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(fact_pattern)},
            ],
            "stream": False,
            "think": False,  # disable thinking mode for Qwen 3.5 / reasoning models
            "options": {
                "temperature": preset.temperature,
                "top_p": preset.top_p,
                "num_predict": preset.max_tokens,
            },
        }

        for attempt in range(_MAX_RETRIES):
            t0 = time.monotonic()
            try:
                resp = requests.post(
                    url,
                    json=payload,
                    timeout=_OLLAMA_TIMEOUT_SECONDS,
                )
            except requests.Timeout:
                raise ModelTimeoutError(
                    f"Ollama API timed out after {_OLLAMA_TIMEOUT_SECONDS}s "
                    f"(model={model_config.model_id})"
                )
            except requests.RequestException as exc:
                raise ModelAPIError(
                    f"Ollama network error for model={model_config.model_id}: {exc}"
                ) from exc

            latency = time.monotonic() - t0

            if resp.status_code == 429:
                if attempt >= _MAX_RETRIES - 1:
                    raise ModelAPIError(
                        f"Ollama rate limit (HTTP 429) exhausted after "
                        f"{_MAX_RETRIES} retries for model={model_config.model_id}."
                    )
                wait = _BACKOFF_SECONDS[attempt]
                logger.warning(
                    "Ollama 429 rate limit (attempt %d/%d, model=%s). "
                    "Waiting %.1fs before retry.",
                    attempt + 1, _MAX_RETRIES, model_config.model_id, wait,
                )
                time.sleep(wait)
                continue

            if resp.status_code == 401:
                raise ModelAPIError(
                    f"Ollama HTTP 401 Unauthorized for model={model_config.model_id}. "
                    f"Cloud-hosted models require Ollama login. Run: ollama login"
                )

            if resp.status_code >= 500:
                raise ModelAPIError(
                    f"Ollama HTTP {resp.status_code} for model={model_config.model_id}: "
                    f"{resp.text[:200]}"
                )

            resp.raise_for_status()

            data = resp.json()
            document_text: str = data["message"]["content"]

            # Fallback: some reasoning models (e.g. Qwen 3.5) put the actual
            # response in "thinking" and leave "content" empty when thinking
            # mode is active. think:false above should prevent this, but guard
            # defensively.
            if not document_text.strip():
                document_text = data["message"].get("thinking", "") or ""
                if document_text:
                    logger.debug(
                        "Ollama: content was empty, fell back to 'thinking' field "
                        "(model=%s)", model_config.model_id
                    )

            # Ollama reports prompt_eval_count + eval_count for total tokens
            prompt_tokens = data.get("prompt_eval_count", 0)
            completion_tokens = data.get("eval_count", 0)
            token_count = prompt_tokens + completion_tokens

            logger.debug(
                "Ollama generate OK: model=%s preset=%s latency=%.2fs tokens=%d",
                model_config.model_id, preset.name, latency, token_count,
            )
            return document_text, latency, token_count

        # Should never reach here
        raise ModelAPIError(
            f"Ollama generate: unexpected retry loop exit for model={model_config.model_id}"
        )
