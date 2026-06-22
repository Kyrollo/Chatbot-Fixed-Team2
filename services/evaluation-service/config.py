"""
config.py
----------
Settings for evaluation-service.

FIX: The original config.py had no database or Redis settings — these were
read raw from os.getenv() inside db/queries.py, which works but means there's
no central place to see what env vars the service needs. Added them here for
visibility. Also added Prometheus port setting.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    SERVICE_NAME: str = "evaluation-service"
    SERVICE_PORT: int = 8005
    HOST: str = "0.0.0.0"

    # ── LLM providers ───────────────────────────────────────────────────────
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    OLLAMA_BASE_URL: str = "http://localhost:11434/v1"
    OLLAMA_MODEL: str = "llama3.2:3b"

    # ── Database ─────────────────────────────────────────────────────────────
    # Mirrors worker-service's resolution order (SYNC_DATABASE_URL →
    # DATABASE_URL → individual POSTGRES_* vars). db/queries.py reads these
    # directly from os.getenv() with the same fallback chain, but listing
    # them here documents what the service needs.
    SYNC_DATABASE_URL: str = ""
    DATABASE_URL: str = ""
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "domain_db"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432

    # ── Redis / Celery ───────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── Evaluation pipeline ──────────────────────────────────────────────────
    EVAL_SCHEDULE_MINUTES: int = 30
    EVAL_LOOKBACK_MINUTES: int = 35
    EVAL_SAMPLE_RATE: float = 0.05
    MODERATION_THRESHOLD: float = 0.6

    # ── RAGAS ────────────────────────────────────────────────────────────────
    RAGAS_JUDGE_MODEL: str = "groq/llama-3.3-70b-versatile"
    RAGAS_EMBEDDING_MODEL: str = "intfloat/multilingual-e5-small"

    # ── Observability ────────────────────────────────────────────────────────
    PROMETHEUS_PORT: int = 9105   # scrape port for this service's /metrics


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
