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

    # LLM Settings — Google Gemini & OpenAI
    GEMINI_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    LLM_PROVIDER: str = "gemini"  # "gemini" or "openai"
    LLM_MODEL: str = "gemini-3.7-flash"  # Default: gemini-3.7-flash, alternative: gemini-3.1-pro-preview
    
    # Gemini Thinking Configuration (https://ai.google.dev/gemini-api/docs/thinking)
    # Supported thinking levels for Gemini 3 series: "LOW", "MEDIUM", "HIGH", "MINIMAL"
    GEMINI_THINKING_LEVEL: str = "HIGH"
    GEMINI_THINKING_BUDGET: int = -1  # For Gemini 2.5 series (-1 = dynamic)

    EMBEDDING_MODEL: str = "text-embedding-004"

    # ChromaDB
    CHROMA_PERSIST_DIR: str = "./chroma_data"

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000", "http://localhost:8080"]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

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


settings = Settings()

# Supported models list for UI and API validation
SUPPORTED_MODELS = [
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
        "id": "gemini-3.1-pro-preview",
        "name": "Gemini 3.1 Pro Preview",
        "provider": "gemini",
        "thinking_supported": True,
        "default_thinking": "HIGH",
        "description": "Advanced reasoning model with Thinking enabled for deep contradiction analysis across complex technical files.",
        "is_default": False,
    },
    {
        "id": "gemini-2.5-flash",
        "name": "Gemini 2.5 Flash",
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
