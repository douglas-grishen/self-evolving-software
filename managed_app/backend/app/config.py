"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Backend settings — values are loaded from environment variables or .env file."""

    # Application
    app_name: str = "Operational Plane"
    app_version: str = "1.1.14"
    debug: bool = False
    environment: str = "development"

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/managed_app"

    # Auth / JWT
    jwt_secret_key: str = "self-evolving-software-genesis-secret-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 480  # 8 hours

    # Default admin (created at first startup)
    default_admin_username: str = "douglas"
    default_admin_password: str = "self-evolving.org"

    # CORS
    allowed_origins: list[str] = ["http://localhost:5173"]

    # External notifications
    notification_webhook_url: str = ""
    notification_webhook_bearer_token: str = ""
    notification_webhook_min_severity: str = "critical"
    notification_webhook_timeout_seconds: float = 5.0

    model_config = {"env_prefix": "APP_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
