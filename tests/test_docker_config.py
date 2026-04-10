"""
tests/test_docker_config.py
Property test for CPU_ONLY_MODE model selection logic.

**Validates: Requirements 15.4**

Property 32: CPU-only mode selects smaller LLM variants
  - When CPU_ONLY_MODE=true, only llama3_8b or qwen2_7b are selected
  - GPU embedding is disabled (EMBEDDING_DEVICE=cpu, OLLAMA_GPU_LAYERS=0)
"""
from __future__ import annotations

import os
import pytest
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Helper: replicate the model-selection logic from entrypoint.sh in Python
# so we can property-test it without shelling out.
# ---------------------------------------------------------------------------

CPU_SAFE_MODELS = {"llama3_8b", "qwen2_7b"}
ALL_MODELS = {"llama3_8b", "qwen2_7b", "mixtral_8x7b"}


def select_model_for_cpu(current_backend: str) -> str:
    """
    Mirror the CPU_ONLY_MODE branch in entrypoint.sh:
    if the current backend is already CPU-safe, keep it;
    otherwise default to llama3_8b.
    """
    if current_backend in CPU_SAFE_MODELS:
        return current_backend
    return "llama3_8b"


def embedding_device_for_cpu(cpu_only: bool) -> str:
    """Return the embedding device string based on CPU_ONLY_MODE."""
    return "cpu" if cpu_only else "gpu"


def gpu_layers_for_cpu(cpu_only: bool) -> int:
    """Return OLLAMA_GPU_LAYERS value based on CPU_ONLY_MODE."""
    return 0 if cpu_only else -1  # -1 = use all available GPU layers


# ---------------------------------------------------------------------------
# Property 32: CPU-only mode selects smaller LLM variants
# ---------------------------------------------------------------------------

@given(current_backend=st.sampled_from(sorted(ALL_MODELS)))
@h_settings(max_examples=50)
def test_cpu_only_mode_selects_small_model(current_backend: str):
    """
    **Validates: Requirements 15.4**

    For any configured LLM backend, enabling CPU_ONLY_MODE must result in
    a CPU-safe model (llama3_8b or qwen2_7b) being selected.
    """
    selected = select_model_for_cpu(current_backend)
    assert selected in CPU_SAFE_MODELS, (
        f"CPU_ONLY_MODE=true selected '{selected}' which is not a CPU-safe model. "
        f"Expected one of {CPU_SAFE_MODELS}."
    )


@given(current_backend=st.sampled_from(sorted(CPU_SAFE_MODELS)))
@h_settings(max_examples=30)
def test_cpu_only_mode_preserves_already_safe_model(current_backend: str):
    """
    **Validates: Requirements 15.4**

    If the configured backend is already CPU-safe, it should be kept as-is
    (no unnecessary downgrade).
    """
    selected = select_model_for_cpu(current_backend)
    assert selected == current_backend, (
        f"CPU_ONLY_MODE=true changed a CPU-safe model '{current_backend}' to '{selected}'. "
        "CPU-safe models should be preserved."
    )


def test_cpu_only_mode_disables_gpu_embedding():
    """
    **Validates: Requirements 15.4**

    When CPU_ONLY_MODE=true, EMBEDDING_DEVICE must be 'cpu' and
    OLLAMA_GPU_LAYERS must be 0.
    """
    assert embedding_device_for_cpu(True) == "cpu"
    assert gpu_layers_for_cpu(True) == 0


def test_gpu_mode_does_not_restrict_embedding():
    """
    **Validates: Requirements 15.4**

    When CPU_ONLY_MODE=false, GPU embedding should not be disabled.
    """
    assert embedding_device_for_cpu(False) != "cpu"
    assert gpu_layers_for_cpu(False) != 0


@given(
    cpu_only=st.booleans(),
    backend=st.sampled_from(sorted(ALL_MODELS)),
)
@h_settings(max_examples=60)
def test_cpu_only_mode_combined_invariants(cpu_only: bool, backend: str):
    """
    **Validates: Requirements 15.4**

    Combined property: when CPU_ONLY_MODE=true, both model selection AND
    embedding device must be CPU-safe regardless of the initial backend.
    """
    if cpu_only:
        selected = select_model_for_cpu(backend)
        device = embedding_device_for_cpu(cpu_only)
        gpu_layers = gpu_layers_for_cpu(cpu_only)

        assert selected in CPU_SAFE_MODELS, (
            f"CPU_ONLY_MODE=true: model '{selected}' is not CPU-safe"
        )
        assert device == "cpu", (
            f"CPU_ONLY_MODE=true: EMBEDDING_DEVICE should be 'cpu', got '{device}'"
        )
        assert gpu_layers == 0, (
            f"CPU_ONLY_MODE=true: OLLAMA_GPU_LAYERS should be 0, got {gpu_layers}"
        )
