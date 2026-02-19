import os
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database - can be set as full URL or individual components
    database_url: Optional[str] = None
    database_user: str = "postgres"
    database_password: str = "password"
    database_host: str = "localhost"
    database_port: str = "5432"
    database_name: str = "sinas"

    # Direct postgres connection (bypasses pgbouncer, used for migrations)
    database_direct_host: Optional[str] = None

    @property
    def get_database_url(self) -> str:
        """Build database URL from components if not explicitly set."""
        if self.database_url:
            return self.database_url
        return f"postgresql://{self.database_user}:{self.database_password}@{self.database_host}:{self.database_port}/{self.database_name}"

    @property
    def get_database_direct_url(self) -> str:
        """Database URL that bypasses pgbouncer (for migrations/DDL)."""
        host = self.database_direct_host or self.database_host
        return f"postgresql://{self.database_user}:{self.database_password}@{host}:{self.database_port}/{self.database_name}"

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

    # Container pool (replaces per-user containers for untrusted execution)
    pool_min_size: int = 4  # Containers to create on startup
    pool_max_size: int = 20  # Maximum pool containers
    pool_min_idle: int = 2  # Trigger replenish when idle drops below this
    pool_max_executions: int = 100  # Recycle container after this many executions
    pool_acquire_timeout: int = 30  # Seconds to wait for a container

    # Package management
    allow_package_installation: bool = True
    allowed_packages: Optional[str] = None  # Comma-separated whitelist, None = all allowed

    # Database pool
    db_pool_size: int = 20  # Connection pool size
    db_max_overflow: int = 30  # Max overflow connections beyond pool_size

    # Docker configuration
    docker_network: str = "auto"  # Docker network for containers (auto-detect or specify)
    default_worker_count: int = 4  # Number of workers to start on backend startup

    # Message history
    max_history_messages: int = 100  # Max messages to load for conversation history

    # Redis & Queue
    redis_url: str = "redis://redis:6379/0"
    queue_function_concurrency: int = 10
    queue_agent_concurrency: int = 5
    queue_default_timeout: int = 300
    queue_max_retries: int = 3
    queue_retry_delay: int = 10

    # Encryption
    encryption_key: Optional[str] = None  # Fernet key for encrypting sensitive data

    # Superadmin
    superadmin_email: Optional[str] = None  # Email for superadmin user

    # Domain (for generating external URLs, e.g., temp file URLs)
    domain: Optional[str] = None  # FQDN like "app.example.com"; localhost or None = no external URLs

    # Declarative Configuration
    config_file: Optional[str] = None  # Path to YAML config file
    auto_apply_config: bool = False  # Auto-apply config file on startup

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"  # Ignore extra env vars like POSTGRES_PASSWORD


settings = Settings()
