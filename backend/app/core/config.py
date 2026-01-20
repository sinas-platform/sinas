from pydantic_settings import BaseSettings
from typing import Optional
import os


class Settings(BaseSettings):
    # Database - can be set as full URL or individual components
    database_url: Optional[str] = None
    database_user: str = "postgres"
    database_password: str = "password"
    database_host: str = "localhost"
    database_port: str = "5432"
    database_name: str = "sinas"

    @property
    def get_database_url(self) -> str:
        """Build database URL from components if not explicitly set."""
        if self.database_url:
            return self.database_url
        return f"postgresql://{self.database_user}:{self.database_password}@{self.database_host}:{self.database_port}/{self.database_name}"

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
    uvicorn_workers: int = 4  # Number of Uvicorn worker processes
    # JWT Token Configuration (Best Practice)
    access_token_expire_minutes: int = 15  # Short-lived access tokens
    refresh_token_expire_days: int = 30  # Long-lived refresh tokens

    # OTP Configuration
    otp_expire_minutes: int = 10

    # External Auth (optional - OIDC/OAuth2)
    external_auth_enabled: bool = False
    oidc_issuer: Optional[str] = None
    oidc_audience: Optional[str] = None
    oidc_groups_claim: str = "groups"

    # Provisioning
    auto_provision_users: bool = True
    auto_provision_groups: bool = False
    default_group_name: str = "Users"

    # SMTP Configuration (for sending emails)
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_domain: Optional[str] = None  # Used for "from" email: login@{smtp_domain}

    # SMTP Server Configuration (for receiving emails)
    smtp_server_host: str = "0.0.0.0"
    smtp_server_port: int = 2525  # Port for incoming email SMTP server

    # Function execution (always uses Docker for isolation)
    function_timeout: int = 300  # 5 minutes (max execution time)
    max_function_memory: int = 512  # MB (Docker memory limit)
    max_function_cpu: float = 1.0  # CPU cores (1.0 = 1 full core, 0.5 = half core)
    max_function_storage: str = "1g"  # Disk storage limit (e.g., "500m", "1g")
    function_container_image: str = "sinas-executor"  # Base image for execution
    function_container_idle_timeout: int = 3600  # Seconds before idle container cleanup (1 hour)

    # Package management
    allow_package_installation: bool = True
    allowed_packages: Optional[str] = None  # Comma-separated whitelist, None = all allowed

    # Encryption
    encryption_key: Optional[str] = None  # Fernet key for encrypting sensitive data

    # Superadmin
    superadmin_email: Optional[str] = None  # Email for superadmin user

    # Declarative Configuration
    config_file: Optional[str] = None  # Path to YAML config file
    auto_apply_config: bool = False  # Auto-apply config file on startup

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"  # Ignore extra env vars like POSTGRES_PASSWORD


settings = Settings()