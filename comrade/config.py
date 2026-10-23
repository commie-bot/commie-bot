import os
from dataclasses import dataclass
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))


DEFAULT_MODELS = {"cerebras": "gpt-oss-120b", "anthropic": "claude-sonnet-5-5"}
DEFAULT_BASE_URLS = {"cerebras": "https://api.cerebras.ai/v1"}


@dataclass
class Config:
    DB_PATH: str = os.getenv("COMRADE_DB", os.path.join(BASE_DIR, "data", "comrade.db"))
    # Catalog files, loaded in order. A later entry with the same id replaces an earlier one.
    CATALOG_DIR: str = os.getenv("COMRADE_CATALOG", os.path.join(BASE_DIR, "catalog"))
    CATALOG_FILES: tuple = ("marxists.yaml", "castro-fbis.yaml", "private.yaml")
    TEXTS_DIR: str = os.getenv("COMRADE_TEXTS", os.path.join(BASE_DIR, "texts"))

    # LLM (quote selection). Provider is "cerebras", "anthropic", or "openai" (any OpenAI-compatible API).
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "cerebras")
    LLM_MODEL: str = os.getenv("LLM_MODEL") or DEFAULT_MODELS.get(LLM_PROVIDER, "")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL") or DEFAULT_BASE_URLS.get(LLM_PROVIDER, "")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")

    # Ollama (embeddings)
    OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    EMBED_MODEL: str = os.getenv("EMBED_MODEL", "nomic-embed-text")

    # Reddit
    REDDIT_CLIENT_ID: str = os.getenv("REDDIT_CLIENT_ID", "")
    REDDIT_CLIENT_SECRET: str = os.getenv("REDDIT_CLIENT_SECRET", "")
    REDDIT_USERNAME: str = os.getenv("REDDIT_USERNAME", "")
    REDDIT_PASSWORD: str = os.getenv("REDDIT_PASSWORD", "")
    REDDIT_USER_AGENT: str = os.getenv("REDDIT_USER_AGENT", "comrade-bot/0.1")

    # Retrieval
    CANDIDATES: int = int(os.getenv("COMRADE_CANDIDATES", "20"))
    REPEAT_DAYS: int = int(os.getenv("COMRADE_REPEAT_DAYS", "30"))
    MAX_SPAN: int = 3  # max sentences per quote
    MAX_QUOTE_CHARS: int = int(os.getenv("COMRADE_MAX_QUOTE_CHARS", "300"))


config = Config()
