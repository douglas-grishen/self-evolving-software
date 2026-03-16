"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Backend settings — values are loaded from environment variables or .env file."""

    # Application
    app_name: str = "Managed App"
    app_version: str = "0.1.0"
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

    model_config = {"env_prefix": "APP_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
