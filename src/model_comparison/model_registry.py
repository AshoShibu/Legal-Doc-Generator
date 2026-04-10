"""
src/model_comparison/model_registry.py

Central registry for all supported LLM models with backend routing and
parameter presets.

Required pipeline (6 models, 3 tiers):
  Tier A — Baseline (Groq cloud):
    llama-3.1-8b, llama-3.3-70b
  Tier B — Small model viability (Ollama local, ~4B params):
    gemma-4-e4b, qwen-3.5-4b
  Tier C — Large model viability (Ollama cloud, no local RAM):
    gemma-4-31b-cloud, qwen-3.5-397b-cloud

All other models are optional and excluded from default runs.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import psutil

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ModelConfig:
    """Configuration for a single LLM model."""
    model_id: str           # identifier used by the provider API
    display_name: str
    provider: str           # "groq" | "ollama"
    api_endpoint: str
    parameter_count: int    # billions
    context_window: int     # max tokens
    required: bool          # True for ≤10B required models; False for optional large models
    ram_required_gb: int    # 0 for cloud-based (Groq); >0 for local Ollama models


@dataclass
class ParameterPreset:
    """Sampling parameter preset for generation."""
    name: str               # "high_quality" | "fast"
    temperature: float
    top_p: float
    max_tokens: int


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

MODEL_REGISTRY: dict[str, ModelConfig] = {
    # -----------------------------------------------------------------------
    # Tier A — Baseline (Groq cloud, required)
    # -----------------------------------------------------------------------
    "llama-3.1-8b": ModelConfig(
        model_id="llama-3.1-8b-instant",
        display_name="Llama 3.1 8B",
        provider="groq",
        api_endpoint="https://api.groq.com/openai/v1",
        parameter_count=8,
        context_window=128_000,
        required=True,
        ram_required_gb=0,  # cloud-based — no local RAM needed
    ),
    "llama-3.3-70b": ModelConfig(
        model_id="llama-3.3-70b-versatile",
        display_name="Llama 3.3 70B",
        provider="groq",
        api_endpoint="https://api.groq.com/openai/v1",
        parameter_count=70,
        context_window=128_000,
        required=True,
        ram_required_gb=0,  # cloud-based — no local RAM needed
    ),
    # -----------------------------------------------------------------------
    # Tier B — Small model viability (Ollama local, ~4B params, required)
    # -----------------------------------------------------------------------
    "gemma-4-e4b": ModelConfig(
        model_id="gemma4:e4b",
        display_name="Gemma 4 E4B",
        provider="ollama",
        api_endpoint="http://localhost:11434",
        parameter_count=4,   # 4.5B effective (8B with embeddings)
        context_window=128_000,
        required=True,
        ram_required_gb=10,  # ~9.6 GB on disk; needs ~10 GB RAM
    ),
    "qwen-3.5-4b": ModelConfig(
        model_id="qwen3.5:4b",
        display_name="Qwen 3.5 4B",
        provider="ollama",
        api_endpoint="http://localhost:11434",
        parameter_count=4,
        context_window=256_000,
        required=True,
        ram_required_gb=4,   # ~3.4 GB on disk; needs ~4 GB RAM
    ),
    # -----------------------------------------------------------------------
    # Tier C — Large model viability (Ollama cloud, required)
    # -----------------------------------------------------------------------
    "gemma-4-31b-cloud": ModelConfig(
        model_id="gemma4:31b-cloud",
        display_name="Gemma 4 31B Cloud (Ollama-hosted)",
        provider="ollama",
        api_endpoint="http://localhost:11434",
        parameter_count=31,
        context_window=256_000,
        required=True,
        ram_required_gb=0,  # Ollama-hosted — no local RAM needed
    ),
    "qwen-3.5-397b-cloud": ModelConfig(
        model_id="qwen3.5:397b-cloud",
        display_name="Qwen 3.5 397B MoE Cloud (Ollama-hosted)",
        provider="ollama",
        api_endpoint="http://localhost:11434",
        parameter_count=397,  # total; 17B active per forward pass (MoE)
        context_window=1_000_000,
        required=True,
        ram_required_gb=0,  # Ollama-hosted — no local RAM needed
    ),
    # -----------------------------------------------------------------------
    # Optional models (excluded from default runs)
    # -----------------------------------------------------------------------
    "qwen-2.5-7b": ModelConfig(
        model_id="qwen2.5:7b",
        display_name="Qwen 2.5 7B (optional)",
        provider="ollama",
        api_endpoint="http://localhost:11434",
        parameter_count=7,
        context_window=32_768,
        required=False,
        ram_required_gb=8,
    ),
    "gemma-4-9b": ModelConfig(
        model_id="gemma4:9b",
        display_name="Gemma 4 9B (optional)",
        provider="ollama",
        api_endpoint="http://localhost:11434",
        parameter_count=9,
        context_window=128_000,
        required=False,
        ram_required_gb=10,
    ),
    "qwen-3-32b": ModelConfig(
        model_id="qwen3:32b",
        display_name="Qwen 3 32B (optional)",
        provider="ollama",
        api_endpoint="http://localhost:11434",
        parameter_count=32,
        context_window=32_768,
        required=False,
        ram_required_gb=20,
    ),
    "gemma-4-31b": ModelConfig(
        model_id="gemma4:31b-instruct",
        display_name="Gemma 4 31B Instruct (optional)",
        provider="ollama",
        api_endpoint="http://localhost:11434",
        parameter_count=31,
        context_window=256_000,
        required=False,
        ram_required_gb=20,
    ),
}

PARAMETER_PRESETS: dict[str, ParameterPreset] = {
    "high_quality": ParameterPreset(
        name="high_quality",
        temperature=0.1,
        top_p=0.9,
        max_tokens=3000,  # full legal document needs ~1500-2500 tokens
    ),
    "fast": ParameterPreset(
        name="fast",
        temperature=0.3,
        top_p=0.95,
        max_tokens=2000,  # raised from 1500 — ensures complete document output
    ),
}


# ---------------------------------------------------------------------------
# RAM detection
# ---------------------------------------------------------------------------

def detect_system_ram_gb() -> int:
    """Return total system RAM in whole gigabytes."""
    return int(psutil.virtual_memory().total // (1024 ** 3))


# ---------------------------------------------------------------------------
# Availability filtering
# ---------------------------------------------------------------------------

def get_available_models(include_large: bool = False) -> list[ModelConfig]:
    """Return models available on this machine.

    Args:
        include_large: When False (default), return only required models
            (≤10B parameters).  When True, detect system RAM and include
            optional large models whose ``ram_required_gb`` fits within
            available RAM.  Cloud-based models (``ram_required_gb == 0``)
            are always included regardless of RAM.

    Returns:
        List of :class:`ModelConfig` instances that can be used on this host.
    """
    if not include_large:
        available = [m for m in MODEL_REGISTRY.values() if m.required]
        logger.debug(
            "get_available_models(include_large=False): %d required models",
            len(available),
        )
        return available

    ram_gb = detect_system_ram_gb()
    logger.info("Detected system RAM: %d GB", ram_gb)

    available = [
        m for m in MODEL_REGISTRY.values()
        if m.ram_required_gb == 0 or m.ram_required_gb <= ram_gb
    ]

    skipped = [
        m.display_name for m in MODEL_REGISTRY.values()
        if m.ram_required_gb > 0 and m.ram_required_gb > ram_gb
    ]
    if skipped:
        logger.warning(
            "Skipping models due to insufficient RAM (%d GB available): %s",
            ram_gb,
            ", ".join(skipped),
        )

    logger.debug(
        "get_available_models(include_large=True): %d/%d models available",
        len(available),
        len(MODEL_REGISTRY),
    )
    return available


# ---------------------------------------------------------------------------
# Backend factory
# ---------------------------------------------------------------------------

def get_backend(model_id: str):
    """Return the appropriate backend instance for *model_id*.

    Imports are deferred to avoid a circular dependency with
    ``src.model_comparison.backends`` (which imports from this module).

    Args:
        model_id: Key in :data:`MODEL_REGISTRY` (e.g. ``"llama-3.1-8b"``).

    Returns:
        A :class:`~src.model_comparison.backends.Groq_Backend` or
        :class:`~src.model_comparison.backends.Ollama_Backend` instance.

    Raises:
        KeyError: If *model_id* is not found in :data:`MODEL_REGISTRY`.
        ValueError: If the model's provider is not ``"groq"`` or ``"ollama"``.
    """
    # Deferred import — backends.py is task 7 and does not exist yet at
    # module-load time.  Importing inside the function body prevents an
    # ImportError when model_registry is imported before backends is created.
    from src.model_comparison.backends import Groq_Backend, Ollama_Backend  # noqa: PLC0415

    config = MODEL_REGISTRY[model_id]  # raises KeyError if unknown

    if config.provider == "groq":
        return Groq_Backend(config)
    if config.provider == "ollama":
        return Ollama_Backend(config)

    raise ValueError(
        f"Unknown provider '{config.provider}' for model '{model_id}'. "
        "Expected 'groq' or 'ollama'."
    )
