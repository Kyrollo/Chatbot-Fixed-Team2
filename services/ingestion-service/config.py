from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:123456@localhost:5432/ragdb"
    redis_url:    str = "redis://localhost:6379/0"
    upload_dir:   str = "./uploads"
    max_size_mb:  int = 50

    class Config:
        env_file = ".env"


settings = Settings()
