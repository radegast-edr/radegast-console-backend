from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./radegast.db"
    secret_key: str = "change-me-in-production"
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
    enable_email_worker: bool = True
    worker_lock_path: str = "/tmp/radegast-console.lock"
    mfa_required_level_admin: str = "none"
    mfa_required_level_maintainer: str = "none"
    mfa_required_level_user: str = "none"
    webauthn_rp_id: str | None = None
    webauthn_origins: str = ""

    model_config = {"env_prefix": "radegast_"}


settings = Settings()

if settings.secret_key == "change-me-in-production":
    print("\n" + "="*80)
    print("WARNING: Using default secret key 'change-me-in-production'!")
    print("This is OK for developing locally but MUST be changed in production.")
    print("="*80 + "\n")
