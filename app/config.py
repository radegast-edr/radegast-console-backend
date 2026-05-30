from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./radegast.db"
    secret_key: str = "change-me-in-production"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000"
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@radegast.local"
    base_url: str = "http://localhost:8000"
    upload_dir: str = "uploads/packs"
    releases_dir: str = "agent/releases"
    session_cookie_name: str = "radegast_session"
    session_max_age: int = 86400 * 7  # 7 days

    model_config = {"env_prefix": "radegast_"}


settings = Settings()
