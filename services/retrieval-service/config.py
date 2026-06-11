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

    # Qdrant (QDRANT_PATH env used by qdrant_client_factory when set)
    QDRANT_URL: str = "http://localhost:6333"

    # Redis + Postgres
    REDIS_URL: str = "redis://localhost:6379/0"
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/domain_db"
    DOMAIN_SERVICE_URL: str = "http://localhost:8001"
    INTERNAL_API_KEY: str = "rag-internal-dev-key-change-in-prod"

    # Retrieval pipeline
    TOP_K_RETRIEVE: int = 20
    TOP_K_RERANK: int = 5
    CACHE_TTL_SECONDS: int = 3600
    #cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
    RERANKER_MODEL: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"


    # Embedding model
    EMBEDDING_MODEL: str = "intfloat/multilingual-e5-small"
    EMBEDDING_DIMENSION: int = 384


settings = Settings()
