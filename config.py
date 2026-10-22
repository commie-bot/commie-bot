import os
from dataclasses import dataclass
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


@dataclass
class Config:
    DB_PATH: str = os.getenv("COMRADE_DB", os.path.join(BASE_DIR, "comrade.db"))
    WORKS_FILE: str = os.getenv("COMRADE_WORKS", os.path.join(BASE_DIR, "works.yaml"))
    TEXTS_DIR: str = os.getenv("COMRADE_TEXTS", os.path.join(BASE_DIR, "texts"))

    # LLM (quote selection). Provider is "anthropic" or "openai" (any OpenAI-compatible API).
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "anthropic")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "claude-sonnet-5-5")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "")
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
    CANDIDATES: int = int(os.getenv("COMRADE_CANDIDATES", "30"))
    REPEAT_DAYS: int = int(os.getenv("COMRADE_REPEAT_DAYS", "30"))
    MAX_SPAN: int = 4  # max sentences per quote


config = Config()
