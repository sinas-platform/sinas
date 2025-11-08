from pydantic_settings import BaseSettings
from typing import Optional
import os


class Settings(BaseSettings):
    # Database
    # Defaults to localhost for local development
    # In docker-compose, DATABASE_URL env var will override this
    database_url: str = "postgresql://postgres:password@localhost:5432/sinas"

    # Redis
    # Hardcoded for docker-compose, localhost for local development
    # Override with REDIS_URL env var if needed
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # ClickHouse
    clickhouse_host: str = os.getenv("CLICKHOUSE_HOST", "localhost")
    clickhouse_port: int = int(os.getenv("CLICKHOUSE_PORT", "8123"))  # HTTP port
    clickhouse_user: str = os.getenv("CLICKHOUSE_USER", "default")
    clickhouse_password: str = os.getenv("CLICKHOUSE_PASSWORD", "")
    clickhouse_database: str = os.getenv("CLICKHOUSE_DATABASE", "sinas")

    # Application
    debug: bool = False
    secret_key: str = "your-secret-key-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 10080  # 7 days

    # OTP Configuration
    otp_expire_minutes: int = 10

    # SMTP Configuration (for sending emails)
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_domain: Optional[str] = None  # Used for "from" email: login@{smtp_domain}

    # SMTP Server Configuration (for receiving emails)
    smtp_server_host: str = "0.0.0.0"
    smtp_server_port: int = 2525  # Port for incoming email SMTP server

    # LLM Provider Configuration
    openai_api_key: Optional[str] = None
    openai_api_base_url: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    local_llm_endpoint: Optional[str] = None
    private_cloud_endpoint: Optional[str] = None
    private_cloud_api_key: Optional[str] = None
    default_llm_provider: str = "openai"
    default_model: Optional[str] = None

    # Function execution
    function_timeout: int = 300  # 5 minutes
    max_function_memory: int = 512  # MB

    # Package management
    allow_package_installation: bool = True

    # Encryption
    encryption_key: Optional[str] = None  # Fernet key for encrypting sensitive data

    # Superadmin
    superadmin_email: Optional[str] = None  # Email for superadmin user

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"  # Ignore extra env vars like POSTGRES_PASSWORD


settings = Settings()