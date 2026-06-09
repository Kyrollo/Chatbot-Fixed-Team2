from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:123456@localhost:5432/domain_db"

    # Keycloak
    KEYCLOAK_ISSUER: str = (
        "http://localhost:8180/realms/rag-system,"
        "http://keycloak:8080/realms/rag-system"
    )
    KEYCLOAK_REALM_URL: str = "http://localhost:8180/realms/rag-system"
    KEYCLOAK_PUBLIC_KEY: str = ""  # PEM body (without header/footer) or full PEM
    KEYCLOAK_CLIENT_ID: str = "domain-service"
    KEYCLOAK_ALGORITHM: str = "RS256"

    # JWT claim that marks a system admin (Keycloak realm role)
    SYSTEM_ADMIN_ROLE: str = "system_admin"

    # Service
    SERVICE_PORT: int = 8001
    SERVICE_NAME: str = "domain-service"

    # Internal service-to-service auth (shared secret for /internal endpoints)
    INTERNAL_API_KEY: str = "change-me-internal-key"

    # Default RAG config values for new domains
    DEFAULT_LLM_ROUTE: str = "default"
    DEFAULT_CHUNK_SIZE: int = 512
    DEFAULT_CHUNK_OVERLAP: int = 64
    DEFAULT_CONFIDENCE_THRESHOLD: float = 0.5


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
