from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Service
    SERVICE_NAME: str = "retrieval-service"
    HOST: str = "0.0.0.0"
    SERVICE_PORT: int = 8003

    # Qdrant (embedded - uses QDRANT_PATH env via qdrant_client_factory)

    # Redis + Postgres
    REDIS_URL: str = "redis://localhost:6379/0"
    DATABASE_URL: str = "postgresql+asyncpg://postgres:55555@localhost:5432/domain_db"
    DOMAIN_SERVICE_URL: str = "http://localhost:8001"
    INTERNAL_API_KEY: str = "rag-internal-dev-key-change-in-prod"

    # Retrieval pipeline
    TOP_K_RETRIEVE: int = 10
    TOP_K_RERANK: int = 3
    CACHE_TTL_SECONDS: int = 3600
    #cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
    RERANKER_MODEL: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"

    # Auth / RBAC (Sprint 2 — RBAC filtering on retrieval)
    KEYCLOAK_ISSUER: str = "http://localhost:8180/realms/rag-system"
    KEYCLOAK_REALM_URL: str = "http://localhost:8180/realms/rag-system"
    KEYCLOAK_CLIENT_ID: str = "domain-service"
    KEYCLOAK_ALGORITHM: str = "RS256"
    KEYCLOAK_PUBLIC_KEY: str = ""
    SYSTEM_ADMIN_ROLE: str = "system_admin"

    # Embedding model
    EMBEDDING_MODEL: str = "intfloat/multilingual-e5-small"
    EMBEDDING_DIMENSION: int = 384

    # LLM Router (for query analysis — reuses same keys as generation-service)
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    GROQ_API_KEY: str = "your-groq-api-key"
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    OLLAMA_BASE_URL: str = "http://localhost:11434/v1"
    OLLAMA_MODEL: str = "llama3.2:3b"


settings = Settings()

