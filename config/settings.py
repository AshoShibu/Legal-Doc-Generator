"""
config/settings.py — Load all environment variables with typed defaults.

Phase 1.5 additions are grouped at the bottom under "Phase 1.5 Feature Flags"
and "Phase 1.5 Subsystem Config".  All new fields default to safe values so
that the Phase 1 pipeline is completely unaffected when the flags are off.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Settings:
    # LLM inference
    ollama_base_url: str = field(default_factory=lambda: os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))
    llm_default_backend: str = field(default_factory=lambda: os.environ.get("LLM_DEFAULT_BACKEND", "groq_llama3_8b"))
    cpu_only_mode: bool = field(default_factory=lambda: os.environ.get("CPU_ONLY_MODE", "false").lower() == "true")

    # Vector store (Phase 2 — FAISS/Qdrant index for Dense/Hybrid RAG)
    # Used by src/rag/vector_store.py. Not needed for Phase 1 CAG pipeline.
    # To re-enable: uncomment these three lines and add the corresponding
    # env vars to .env before running Phase 2 corpus ingestion.
    # vector_store_type: str = field(default_factory=lambda: os.environ.get("VECTOR_STORE_TYPE", "qdrant"))
    # qdrant_url: str = field(default_factory=lambda: os.environ.get("QDRANT_URL", "http://localhost:6333"))
    # faiss_index_path: str = field(default_factory=lambda: os.environ.get("FAISS_INDEX_PATH", "./data/faiss_index"))

    # Dataset paths
    dataset_root: str = field(default_factory=lambda: os.environ.get("DATASET_ROOT", "./Maharashtra Legal Document Dataset"))
    templates_dir: str = field(default_factory=lambda: os.environ.get("TEMPLATES_DIR", "./Legal Document Templates"))

    # Output
    output_dir: str = field(default_factory=lambda: os.environ.get("OUTPUT_DIR", "./output"))
    log_db_path: str = field(default_factory=lambda: os.environ.get("LOG_DB_PATH", "./output/runs.db"))
    log_retention_days: int = field(default_factory=lambda: int(os.environ.get("LOG_RETENTION_DAYS", "30")))

    # Frontend
    frontend_port: int = field(default_factory=lambda: int(os.environ.get("FRONTEND_PORT", "8501")))
    llm_api_port: int = field(default_factory=lambda: int(os.environ.get("LLM_API_PORT", "8000")))

    # Embedding (Phase 2 — RAG corpus ingestion)
    # Used by src/rag/embedder.py to select bge-m3 (English) or LaBSE (Marathi/Bilingual).
    # Not needed for Phase 1 CAG pipeline.
    # embedding_model: str = field(default_factory=lambda: os.environ.get("EMBEDDING_MODEL", "bge-m3"))
    # embedding_batch_size: int = field(default_factory=lambda: int(os.environ.get("EMBEDDING_BATCH_SIZE", "32")))
    embedding_model: str = field(default_factory=lambda: os.environ.get("EMBEDDING_MODEL", "bge-m3"))
    embedding_batch_size: int = field(default_factory=lambda: int(os.environ.get("EMBEDDING_BATCH_SIZE", "32")))

    # Hybrid RAG (Phase 2 — weighted dense+BM25 retrieval)
    # alpha controls the dense vs BM25 score blend: score = alpha*dense + (1-alpha)*bm25.
    # Used by src/rag/hybrid_retriever.py. Not needed for Phase 1.
    # hybrid_alpha: float = field(default_factory=lambda: float(os.environ.get("HYBRID_ALPHA", "0.6")))
    hybrid_alpha: float = field(default_factory=lambda: float(os.environ.get("HYBRID_ALPHA", "0.6")))

    # Evaluation query set (Phase 2 — cross-pipeline benchmarking runner)
    # Used by scripts/run_evaluation.py. Phase 1 uses scripts/run_cag_evaluation.py instead.
    # eval_query_set_path: str = field(default_factory=lambda: os.environ.get("EVAL_QUERY_SET_PATH", "./config/eval_queries.json"))

    # -----------------------------------------------------------------------
    # Phase 1.5 — Feature Flags
    # -----------------------------------------------------------------------
    # Master switch: when False, all Phase 1.5 features are disabled regardless
    # of the individual flags below.
    enable_phase_1_5: bool = field(
        default_factory=lambda: os.environ.get("ENABLE_PHASE_1_5", "true").lower() == "true"
    )
    enable_citation_formatting: bool = field(
        default_factory=lambda: os.environ.get("ENABLE_CITATION_FORMATTING", "true").lower() == "true"
    )
    enable_underline_formatting: bool = field(
        default_factory=lambda: os.environ.get("ENABLE_UNDERLINE_FORMATTING", "true").lower() == "true"
    )
    enable_model_comparison: bool = field(
        default_factory=lambda: os.environ.get("ENABLE_MODEL_COMPARISON", "true").lower() == "true"
    )
    enable_chat_backend: bool = field(
        default_factory=lambda: os.environ.get("ENABLE_CHAT_BACKEND", "true").lower() == "true"
    )

    # -----------------------------------------------------------------------
    # Phase 1.5 — Model Comparison
    # -----------------------------------------------------------------------
    model_comparison_output_dir: str = field(
        default_factory=lambda: os.environ.get("MODEL_COMPARISON_OUTPUT_DIR", "./output/model_comparison")
    )
    include_large_models: bool = field(
        default_factory=lambda: os.environ.get("INCLUDE_LARGE_MODELS", "false").lower() == "true"
    )

    # -----------------------------------------------------------------------
    # Phase 1.5 — Chat Backend
    # -----------------------------------------------------------------------
    chat_backend_port: int = field(
        default_factory=lambda: int(os.environ.get("CHAT_BACKEND_PORT", "8000"))
    )
    chat_session_store: str = field(
        default_factory=lambda: os.environ.get("CHAT_SESSION_STORE", "memory")
    )
    redis_url: str = field(
        default_factory=lambda: os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    )
    chat_session_ttl_minutes: int = field(
        default_factory=lambda: int(os.environ.get("CHAT_SESSION_TTL_MINUTES", "30"))
    )
    cors_origins: str = field(
        default_factory=lambda: os.environ.get("CORS_ORIGINS", "http://localhost:3000,http://localhost:8501")
    )
    rate_limit_per_minute: int = field(
        default_factory=lambda: int(os.environ.get("RATE_LIMIT_PER_MINUTE", "100"))
    )
    rate_limit_per_hour: int = field(
        default_factory=lambda: int(os.environ.get("RATE_LIMIT_PER_HOUR", "1000"))
    )
    admin_api_key: str = field(
        default_factory=lambda: os.environ.get("ADMIN_API_KEY", "change-me-before-deploying")
    )
    enable_https: bool = field(
        default_factory=lambda: os.environ.get("ENABLE_HTTPS", "false").lower() == "true"
    )

    def log_feature_flags(self) -> None:
        """Log all Phase 1.5 feature flag states at startup for debugging."""
        logger.info("=== Phase 1.5 Feature Flags ===")
        logger.info("  ENABLE_PHASE_1_5            = %s", self.enable_phase_1_5)
        if not self.enable_phase_1_5:
            logger.info("  (all Phase 1.5 features disabled by master switch)")
            return
        logger.info("  ENABLE_CITATION_FORMATTING  = %s", self.enable_citation_formatting)
        logger.info("  ENABLE_UNDERLINE_FORMATTING = %s", self.enable_underline_formatting)
        logger.info("  ENABLE_MODEL_COMPARISON     = %s", self.enable_model_comparison)
        logger.info("  ENABLE_CHAT_BACKEND         = %s", self.enable_chat_backend)
        logger.info("=== Phase 1.5 Model Comparison ===")
        logger.info("  MODEL_COMPARISON_OUTPUT_DIR = %s", self.model_comparison_output_dir)
        logger.info("  INCLUDE_LARGE_MODELS        = %s", self.include_large_models)
        logger.info("=== Phase 1.5 Chat Backend ===")
        logger.info("  CHAT_BACKEND_PORT           = %s", self.chat_backend_port)
        logger.info("  CHAT_SESSION_STORE          = %s", self.chat_session_store)
        logger.info("  CHAT_SESSION_TTL_MINUTES    = %s", self.chat_session_ttl_minutes)
        logger.info("  CORS_ORIGINS                = %s", self.cors_origins)
        logger.info("  RATE_LIMIT_PER_MINUTE       = %s", self.rate_limit_per_minute)
        logger.info("  RATE_LIMIT_PER_HOUR         = %s", self.rate_limit_per_hour)
        logger.info("  ENABLE_HTTPS                = %s", self.enable_https)

    # Convenience helpers — respect the master switch
    @property
    def citation_formatting_enabled(self) -> bool:
        return self.enable_phase_1_5 and self.enable_citation_formatting

    @property
    def underline_formatting_enabled(self) -> bool:
        return self.enable_phase_1_5 and self.enable_underline_formatting

    @property
    def model_comparison_enabled(self) -> bool:
        return self.enable_phase_1_5 and self.enable_model_comparison

    @property
    def chat_backend_enabled(self) -> bool:
        return self.enable_phase_1_5 and self.enable_chat_backend


# Module-level singleton — import and use directly
settings = Settings()
