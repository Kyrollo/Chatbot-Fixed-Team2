from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql+asyncpg://postgres:55555@localhost:5432/domain_db"
    sync_database_url: str = "postgresql://postgres:55555@localhost:5432/domain_db"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # File upload
    upload_dir: str = "data/uploads"
    max_size_mb: int = 50

    # Keycloak JWT validation (same realm as domain-service)
    KEYCLOAK_ISSUER: str = "http://localhost:8180/realms/rag-system"
    KEYCLOAK_REALM_URL: str = "http://localhost:8180/realms/rag-system"
    KEYCLOAK_CLIENT_ID: str = "domain-service"
    KEYCLOAK_ALGORITHM: str = "RS256"
    KEYCLOAK_PUBLIC_KEY: str = ""

    # Internal service auth
    INTERNAL_API_KEY: str = "rag-internal-dev-key-change-in-prod"

    # Domain service URL for RBAC checks
    DOMAIN_SERVICE_URL: str = "http://localhost:8001"

    # Keycloak system admin role name
    SYSTEM_ADMIN_ROLE: str = "system_admin"


settings = Settings()
