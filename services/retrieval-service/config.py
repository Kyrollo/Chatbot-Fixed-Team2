import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Service

    # Service
    SERVICE_NAME: str = "retrieval-service"
    HOST: str = "0.0.0.0"
    SERVICE_PORT: int = 8003

    # Qdrant (embedded - uses QDRANT_PATH env via qdrant_client_factory)

    # Redis + Postgres
    REDIS_URL: str = f"redis://localhost:{os.getenv('REDIS_PORT', '6379')}/0"
    DATABASE_URL: str = f"postgresql+asyncpg://postgres:postgres@localhost:{os.getenv('POSTGRES_PORT', '5432')}/domain_db"
    DOMAIN_SERVICE_URL: str = "http://localhost:8001"
    INTERNAL_API_KEY: str = "rag-internal-dev-key-change-in-prod"

    # Retrieval pipeline
    TOP_K_RETRIEVE: int = 10
    TOP_K_RERANK: int = 3
    RERANKER_CANDIDATE_CAP: int = 15
    CACHE_TTL_SECONDS: int = 3600
    MODELS_DIR: str = "D:\\Personal\\Fixed Solutions\\Project Files\\v5\\models"
    EMBEDDING_MODEL: str = "D:\\Personal\\Fixed Solutions\\Project Files\\v5\\models\\e5-small"
    EMBEDDING_DIMENSION: int = 384
    RERANKER_MODEL: str = (
        "D:\\Personal\\Fixed Solutions\\Project Files\\v5\\models\\huggingface\\hub\\"
        "models--cross-encoder--mmarco-mMiniLMv2-L12-H384-v1\\snapshots\\"
        "1427fd652930e4ba29e8149678df786c240d8825"
    )
    ENABLE_RERANKER: bool = True
    RERANKER_KEEP_LOADED: bool = True
    RERANKER_IDLE_TIMEOUT_SECONDS: int = 1800
    RETRIEVAL_WARMUP_ON_START: bool = True
    WARMUP_EMBEDDING: bool = True
    WARMUP_RERANKER: bool = False

    # Auth / RBAC (Sprint 2 — RBAC filtering on retrieval)
    KEYCLOAK_ISSUER: str = f"http://localhost:{os.getenv('KEYCLOAK_PORT', '8180')}/realms/rag-system"
    KEYCLOAK_REALM_URL: str = f"http://localhost:{os.getenv('KEYCLOAK_PORT', '8180')}/realms/rag-system"
    KEYCLOAK_CLIENT_ID: str = "domain-service"
    KEYCLOAK_ALGORITHM: str = "RS256"
    KEYCLOAK_PUBLIC_KEY: str = ""
    SYSTEM_ADMIN_ROLE: str = "system_admin"

    # LLM Router (for query analysis — reuses same keys as generation-service)
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    OLLAMA_BASE_URL: str = "http://localhost:11434/v1"
    OLLAMA_MODEL: str = "llama3.2:3b"

    # Apache AGE Graph Settings
    AGE_DATABASE_DSN: str = ""
    AGE_GRAPH_NAME: str = "rag_graph"
    QUERY_NER_MODE: str = "rules_first"
    GRAPH_ENTITY_MATCH_THRESHOLD: float = 0.72
    GRAPH_MAX_MATCHED_ENTITIES: int = 5


settings = Settings()

