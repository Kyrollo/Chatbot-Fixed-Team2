from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # No defaults — must come from environment / .env
    # Docker Compose injects these; missing values crash early (intentional)
    database_url: str ="postgresql+asyncpg://postgres:123456@localhost:5432/domain_db"
    redis_url:    str  ="redis://localhost:6379/0"
    upload_dir:   str = "/data/uploads"
    max_size_mb:  int = 50

    class Config:
        env_file = ".env"


settings = Settings()