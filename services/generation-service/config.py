from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
    )

    SERVICE_NAME: str = "generation-service"
    SERVICE_PORT: int = 8004
    HOST: str = "0.0.0.0"

    REDIS_URL: str = "redis://localhost:6379/0"
    DATABASE_URL: str = "postgresql+asyncpg://postgres:55555@localhost:5432/domain_db"
    DOMAIN_SERVICE_URL: str = "http://localhost:8001"
    INTERNAL_API_KEY: str = "rag-internal-dev-key-change-in-prod"
    SYSTEM_ADMIN_ROLE: str = "system_admin"

    # Retrieval service
    RETRIEVAL_SERVICE_URL: str = "http://localhost:8003"

    KEYCLOAK_ISSUER: str = "http://localhost:8180/realms/rag-system"
    KEYCLOAK_REALM_URL: str = "http://localhost:8180/realms/rag-system"
    KEYCLOAK_CLIENT_ID: str = "domain-service"
    KEYCLOAK_ALGORITHM: str = "RS256"
    KEYCLOAK_PUBLIC_KEY: str = ""

    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    OLLAMA_BASE_URL: str = "http://localhost:11434/v1"
    OLLAMA_MODEL: str = "llama3.2:3b"

    CACHE_TTL_SECONDS: int = 3600
    TOP_K_RETRIEVE: int = 20
    TOP_K_RERANK: int = 5
    DEFAULT_MAX_TOKENS: int = 512
    DEFAULT_TEMPERATURE: float = 0.2


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
