from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Service
    SERVICE_NAME: str = "retrieval-service"
    HOST: str = "0.0.0.0"
    PORT: int = 8003

    # Qdrant
    QDRANT_HOST: str = "qdrant"
    QDRANT_PORT: int = 6333

    # Embedding model
    EMBEDDING_MODEL: str = "intfloat/multilingual-e5-base"
    EMBEDDING_DIMENSION: int = 768

    class Config:
        env_file = ".env"


settings = Settings()
