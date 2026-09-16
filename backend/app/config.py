"""TraceAudit AI — Application configuration."""

import os
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Central configuration loaded from environment / .env file."""

    # Application
    APP_NAME: str = "TraceAudit AI"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./traceaudit.db"

    # File storage (local filesystem for MVP)
    UPLOAD_DIR: str = "./uploads"

    # Frontend static files directory (for single-server serving in Databricks Apps)
    FRONTEND_DIR: str = os.getenv("FRONTEND_DIR", "../dist/client")
    ROOT_PATH: str = os.getenv("DATABRICKS_APP_ROOT_PATH", os.getenv("ROOT_PATH", ""))

    # Layout parser used by document ingestion. ``databricks-auto`` uses
    # ai_parse_document when configured and falls back to the local parser if
    # the remote service is unavailable.
    TRACEAUDIT_DOCUMENT_PARSER: str = "auto"

    # CORS — allowed frontend origins (JSON list in .env overrides this default)
    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
        "http://localhost:8080",
    ]

    # LLM Settings — Google Gemini & OpenAI
    GEMINI_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    DASHSCOPE_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    HF_TOKEN: str = ""
    HUGGINGFACE_TOKEN: str = ""

    # On-demand technical-figure description cascade. OpenRouter is tried
    # first; Gemini, Groq, and Hugging Face remain bounded fallbacks.
    OPENROUTER_VISION_MODEL: str = "openrouter/free"
    GEMINI_VISION_MODEL: str = "models/gemini-3.6-flash"
    GROQ_VISION_MODEL: str = "qwen/qwen3.6-27b"
    GROQ_TEXT_MODEL: str = "qwen/qwen3.8-27b"
    HF_VISION_MODEL: str = "Qwen/Qwen2.5-VL-3B-Instruct"

    LLM_PROVIDER: str = "gemini"  # "gemini", "groq", "databricks", or "openai"
    LLM_MODEL: str = "gemini-3.7-flash"  # Default: gemini-3.7-flash, alternative: gemini-3.1-pro-preview

    # Requirement discovery can use the caller-selected model (Maverick in the
    # extraction benchmark), while semantic planning and atomic construction use
    # a reasoning-focused model.  The fallback is stage-local and is attempted
    # only when the primary atomic model returns no schema-valid response.
    # TokenRouter's configured GLM 5.3 route avoids Gemini quota contention for
    # semantic planning and atomic contract construction.
    ATOMIC_DECOMPOSITION_MODEL: str = "z-ai/glm-5.3-free"
    ATOMIC_DECOMPOSITION_FALLBACK_MODEL: str = "system.ai.llama-4-maverick"
    ATOMIC_DECOMPOSITION_THINKING_LEVEL: str = "PROVIDER_DEFAULT"
    # Keep the former planner available for controlled extraction comparisons.
    ATOMIC_CONSTRUCTION_MODE: str = "direct"
    # Global cap for independent per-requirement contract calls. Repairs reuse
    # the same slots so a failing section cannot create an endpoint burst.
    ATOMIC_CONTRACT_CONCURRENCY: int = 4

    # Process-wide outbound capacity. Stage-specific limits below prevent one
    # audit from monopolising all model or reranker slots when several projects
    # run at the same time.
    LLM_GLOBAL_CONCURRENCY: int = 8
    AUDIT_RETRIEVAL_CONCURRENCY: int = 4
    AUDIT_VERIFICATION_BATCH_CONCURRENCY: int = 2
    AUDIT_CITATION_CONCURRENCY: int = 4
    AUDIT_RETRY_CONCURRENCY: int = 3
    AUDIT_RERANKER_CONCURRENCY: int = 4
    AUDIT_WORKER_CONCURRENCY: int = 2
    AUDIT_WORKER_POLL_SECONDS: float = 1.0
    AUDIT_CACHE_VERSION: str = "audit-v1"
    AUDIT_CACHE_MAX_ENTRIES: int = 50_000
    
    # Gemini Thinking Configuration (https://ai.google.dev/gemini-api/docs/thinking)
    # Supported thinking levels for Gemini 3 series: "LOW", "MEDIUM", "HIGH", "MINIMAL"
    GEMINI_THINKING_LEVEL: str = "HIGH"
    GEMINI_THINKING_BUDGET: int = -1  # For Gemini 2.5 series (-1 = dynamic)

    # Databricks AI Gateway Settings (MLflow Model Serving)
    DATABRICKS_TOKEN: str = ""
    DATABRICKS_BASE_URL: str = ""  # e.g. "https://<workspace-id>.cloud.databricks.com/ai-gateway/mlflow/v1"
    DATABRICKS_MODEL: str = "system.ai.llama-4-maverick"
    DATABRICKS_VISION_MODEL: str = "system.ai.llama-4-maverick"
    DATABRICKS_FALLBACK_MODELS: list[str] = []
    DATABRICKS_REASONING_TIMEOUT_SECONDS: float = 300.0
    DATABRICKS_HOST: str = ""
    DATABRICKS_SQL_WAREHOUSE_ID: str = ""
    DATABRICKS_DOCUMENT_VOLUME: str = ""
    DATABRICKS_DOCUMENT_CACHE_DIR: str = ""
    DATABRICKS_TARGETED_EXTRACTION_ENABLED: bool = False
    DATABRICKS_TARGETED_EXTRACTION_CONCURRENCY: int = 2
    DATABRICKS_TARGETED_EXTRACTION_MIN_CONFIDENCE: float = 0.80
    # ai_extract evidence discovery scans the independent-evidence corpus before
    # verification. Normal-sized reports fit in one request; larger corpora are
    # split without dropping tail pages. A single focused retry is permitted for
    # atomic conditions that the first pass did not locate.
    DATABRICKS_TARGETED_EXTRACTION_MAX_INPUT_CHARS: int = 800_000
    DATABRICKS_TARGETED_EXTRACTION_MAX_CANDIDATES: int = 24
    DATABRICKS_TARGETED_EXTRACTION_RETRY_UNCOVERED: bool = True

    # Optional Databricks-native retrieval stack.  Every feature is opt-in so
    # local BM25/Gemini retrieval remains a reproducible benchmark control.
    DATABRICKS_AI_PREP_SEARCH_ENABLED: bool = False
    DATABRICKS_AI_PREP_SEARCH_REQUIRED: bool = False
    DATABRICKS_AI_SEARCH_ENABLED: bool = False
    DATABRICKS_AI_SEARCH_ENDPOINT: str = ""
    DATABRICKS_AI_SEARCH_INDEX: str = ""
    DATABRICKS_AI_SEARCH_SOURCE_TABLE: str = ""
    DATABRICKS_AI_SEARCH_EMBEDDING_MODEL: str = "databricks-gte-large-en"
    DATABRICKS_AI_SEARCH_RERANK_ENABLED: bool = True
    DATABRICKS_AI_SEARCH_CANDIDATE_COUNT: int = 30
    DATABRICKS_AI_SEARCH_MAX_QUERIES_PER_REQUIREMENT: int = 8
    DATABRICKS_AI_SEARCH_SYNC_TIMEOUT_SECONDS: float = 90.0
    DATABRICKS_CUSTOM_RERANKER_ENABLED: bool = False
    DATABRICKS_CUSTOM_RERANKER_ENDPOINT: str = "bge-reranker-v2-m3"
    DATABRICKS_CUSTOM_RERANKER_CANDIDATE_COUNT: int = 20
    DATABRICKS_CUSTOM_RERANKER_MAX_QUERIES: int = 1
    DATABRICKS_CUSTOM_RERANKER_TIMEOUT_SECONDS: float = 180.0
    DATABRICKS_CUSTOM_RERANKER_BATCH_SIZE: int = 4

    # Optional MLflow observability.  The dependency is loaded lazily, keeping
    # development and test environments usable without MLflow installed.
    DATABRICKS_MLFLOW_TRACING_ENABLED: bool = False
    DATABRICKS_MLFLOW_EXPERIMENT: str = ""

    # TokenRouter Settings (Multi-Model OpenAI-Compatible Gateway)
    TOKENROUTER_API_KEY: str = ""
    TOKENROUTER_BASE_URL: str = "https://api.tokenrouter.com/v1"
    TOKENROUTER_MODEL: str = "z-ai/glm-5.3-free"

    # Alibaba Cloud Model Studio (OpenAI Responses-compatible endpoint)
    DASHSCOPE_BASE_URL: str = "https://dashscope-intl.aliyuncs.com/api/v2/apps/protocols/compatible-mode/v1"
    DASHSCOPE_MODEL: str = "qwen3.8-flash"

    # OpenRouter (vision only; the free router chooses an image-capable model)
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

    # OpenAI-compatible Base URL
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"

    # A different model reviews only condition decisions that remain
    # internally inconsistent after the primary model's focused retry.  It is
    # not a blanket ensemble and Python never substitutes a semantic label.
    SECONDARY_ADJUDICATOR_ENABLED: bool = True
    SECONDARY_ADJUDICATOR_MODEL: str = ""

    # Extractive citation grounding runs only when an attributed condition's
    # current quote is not a literal span of its cited evidence excerpt.
    CITATION_GROUNDING_ENABLED: bool = True
    CITATION_GROUNDING_MODEL: str = ""

    model_config = {
        "env_file": (
            str(Path(__file__).resolve().parent.parent / ".env"),
            ".env",
        ),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @property
    def effective_gemini_api_key(self) -> str:
        """Return the active Gemini API key from GEMINI_API_KEY, GOOGLE_API_KEY, or OPENAI_API_KEY if prefixed/used."""
        key = self.GEMINI_API_KEY or self.GOOGLE_API_KEY
        if not key and self.OPENAI_API_KEY and self.OPENAI_API_KEY.startswith("AIza"):
            key = self.OPENAI_API_KEY
        if not key:
            key = os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
        return key

    @property
    def effective_openai_api_key(self) -> str:
        """Return OpenAI API key if present and starts with sk-."""
        if self.OPENAI_API_KEY and self.OPENAI_API_KEY.startswith("sk-"):
            return self.OPENAI_API_KEY
        return os.environ.get("OPENAI_API_KEY", "")

    @property
    def effective_openai_base_url(self) -> str:
        """Return OpenAI base URL without trailing slash."""
        url = self.OPENAI_BASE_URL or os.environ.get("OPENAI_BASE_URL", "") or "https://api.openai.com/v1"
        return url.rstrip("/")

    @property
    def effective_tokenrouter_api_key(self) -> str:
        """Return TokenRouter API key from TOKENROUTER_API_KEY or environment."""
        return self.TOKENROUTER_API_KEY or os.environ.get("TOKENROUTER_API_KEY", "")

    @property
    def effective_tokenrouter_base_url(self) -> str:
        """Return TokenRouter base URL without trailing slash."""
        url = self.TOKENROUTER_BASE_URL or os.environ.get("TOKENROUTER_BASE_URL", "") or "https://api.tokenrouter.com/v1"
        return url.rstrip("/")

    @property
    def effective_dashscope_api_key(self) -> str:
        """Return the DashScope key without exposing it to diagnostics."""
        return self.DASHSCOPE_API_KEY or os.environ.get("DASHSCOPE_API_KEY", "")

    @property
    def effective_openrouter_api_key(self) -> str:
        """Return the OpenRouter credential used only by the vision cascade."""
        return self.OPENROUTER_API_KEY or os.environ.get("OPENROUTER_API_KEY", "")

    @property
    def effective_dashscope_base_url(self) -> str:
        """Return the DashScope Responses-compatible base URL."""
        url = (
            self.DASHSCOPE_BASE_URL
            or os.environ.get("DASHSCOPE_BASE_URL", "")
            or "https://dashscope-intl.aliyuncs.com/api/v2/apps/protocols/compatible-mode/v1"
        )
        return url.rstrip("/")

    @property
    def effective_groq_api_key(self) -> str:
        """Return the active Groq API key without exposing it to diagnostics."""
        return self.GROQ_API_KEY or os.environ.get("GROQ_API_KEY", "")

    @property
    def effective_hf_token(self) -> str:
        """Return a Hugging Face Inference Providers token."""
        return (
            self.HF_TOKEN
            or self.HUGGINGFACE_TOKEN
            or os.environ.get("HF_TOKEN", "")
            or os.environ.get("HUGGINGFACE_TOKEN", "")
        )

    @property
    def effective_databricks_token(self) -> str:
        """Return Databricks token from DATABRICKS_TOKEN or environment."""
        return self.DATABRICKS_TOKEN or os.environ.get("DATABRICKS_TOKEN", "")


settings = Settings()

# Supported models list for UI and API validation
SUPPORTED_MODELS = [
    {
        "id": "qwen3.8-flash",
        "name": "Qwen 3.8 Flash (DashScope)",
        "provider": "dashscope",
        "thinking_supported": True,
        "default_thinking": "MEDIUM",
        "description": "Alibaba Cloud Model Studio Responses endpoint with thinking enabled for structured reasoning.",
        "is_default": False,
    },
    {
        "id": "gemini-3.8-flash",
        "name": "Gemini 3.8 Flash",
        "provider": "gemini",
        "thinking_supported": True,
        "default_thinking": "MEDIUM",
        "description": "Reasoning model used by default for semantic clause planning and atomic decomposition.",
        "is_default": False,
    },
    {
        "id": "gemini-3.7-flash",
        "name": "Gemini 3.7 Flash",
        "provider": "gemini",
        "thinking_supported": True,
        "default_thinking": "HIGH",
        "description": "Recommended. Ultra-fast, highly accurate extraction with High Thinking reasoning enabled.",
        "is_default": True,
    },
    {
        "id": "z-ai/glm-5.3-flash",
        "name": "GLM 5.3 Flash (TokenRouter)",
        "provider": "tokenrouter",
        "thinking_supported": False,
        "description": "Fast GLM 5.3 endpoint for semantic planning and structured atomic decomposition.",
        "is_default": False,
    },
    {
        "id": "z-ai/glm-5.3-free",
        "name": "GLM 5.3 (TokenRouter)",
        "provider": "tokenrouter",
        "thinking_supported": False,
        "description": "High-performance GLM 5.3 model hosted via TokenRouter OpenAI-compatible gateway.",
        "is_default": False,
    },
    {
        "id": "system.ai.llama-4-maverick",
        "name": "Databricks Llama 4 Maverick",
        "provider": "databricks",
        "thinking_supported": False,
        "description": "Primary model for the multimodal diagnostic benchmark; supports controlled comparison with the existing Databricks models.",
        "is_default": False,
    },
    {
        "id": "system.ai.qwen35-122b-a10b",
        "name": "Databricks Qwen 3.5 122B",
        "provider": "databricks",
        "thinking_supported": False,
        "description": "Databricks 122B parameter technical reasoning model for dense verification.",
        "is_default": False,
    },
    {
        "id": "system.ai.gpt-oss-120b",
        "name": "Databricks GPT-OSS 120B",
        "provider": "databricks",
        "thinking_supported": True,
        "default_thinking": "MEDIUM",
        "description": "Reasoning model for atomic contract construction and evidence verification.",
        "is_default": False,
    },
    {
        "id": "system.ai.meta-llama-3-3-70b-instruct",
        "name": "Databricks Llama 3.3 70B",
        "provider": "databricks",
        "thinking_supported": False,
        "description": "Databricks Meta Llama 3.3 70B model for strict compliance matrix auditing.",
        "is_default": False,
    },
    {
        "id": "gemini-3.1-pro-preview",
        "name": "Gemini 3.1 Pro Preview",
        "provider": "gemini",
        "thinking_supported": True,
        "default_thinking": "HIGH",
        "description": "Advanced reasoning model with Thinking enabled for deep contradiction analysis across complex technical files.",
        "is_default": False,
    },
    {
        "id": "gemini-3.6-flash",
        "name": "Gemini 3.6 Flash",
        "provider": "gemini",
        "thinking_supported": True,
        "default_thinking": "MEDIUM",
        "description": "Fast production model with dynamic reasoning budget.",
        "is_default": False,
    },
    {
        "id": "gemini-2.5-pro",
        "name": "Gemini 2.5 Pro",
        "provider": "gemini",
        "thinking_supported": True,
        "default_thinking": "HIGH",
        "description": "Comprehensive engineering reasoning and multilingual standards analysis.",
        "is_default": False,
    },
    {
        "id": "gpt-4o-mini",
        "name": "GPT-4o Mini",
        "provider": "openai",
        "thinking_supported": False,
        "default_thinking": "NONE",
        "description": "OpenAI lightweight model (requires OpenAI API key).",
        "is_default": False,
    },
    {
        "id": "gpt-4o",
        "name": "GPT-4o",
        "provider": "openai",
        "thinking_supported": False,
        "default_thinking": "NONE",
        "description": "OpenAI flagship reasoning model (requires OpenAI API key).",
        "is_default": False,
    },
]

SUPPORTED_THINKING_LEVELS = ["LOW", "MEDIUM", "HIGH", "MINIMAL"]

# Ensure upload directory exists
Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
