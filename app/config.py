from typing import Literal
from pydantic_settings import BaseSettings


DEFAULT_SECRET_KEY = "change-me-in-production"


class Settings(BaseSettings):
    environment: Literal["dev", "prod"] = "prod"
    database_url: str = "sqlite+aiosqlite:///./radegast.db"
    secret_key: str = DEFAULT_SECRET_KEY
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000,https://localhost:8000,https://127.0.0.1:8000,https://localhost:5173,https://127.0.0.1:5173"
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
    turnstile_site_key: str | None = None
    turnstile_secret_key: str | None = None
    email_debounce_seconds: int = 180
    email_bulk_intervals: str = "3,3,6,16,37,62,122,193"
    email_bulk_reset_hours: int = 24
    enable_email_worker: bool = True
    worker_lock_path: str = "/tmp/radegast-console.lock"
    mfa_required_level_admin: str = "none"
    mfa_required_level_maintainer: str = "none"
    mfa_required_level_user: str = "none"
    webauthn_rp_id: str | None = None
    webauthn_origins: str = ""
    web_ui_url: str | None = None

    model_config = {"env_prefix": "radegast_"}


settings = Settings()

if settings.secret_key == DEFAULT_SECRET_KEY and settings.environment != "dev":
    print("\n" + "="*80)
    print(f"WARNING: Using default secret key '{DEFAULT_SECRET_KEY}'!")
    print("This is OK for developing locally but MUST be changed in production.")
    print("If you are developing, set environment variable RADEGAST_ENVIRONMENT=dev to hide this warning")
    print("="*80 + "\n")
