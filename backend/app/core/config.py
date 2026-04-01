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
    clickhouse_retention_days: int = int(os.getenv("CLICKHOUSE_RETENTION_DAYS", "90"))
    clickhouse_hot_retention_days: int = int(os.getenv("CLICKHOUSE_HOT_RETENTION_DAYS", "30"))

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
    otp_max_attempts: int = 2  # Max verification attempts before OTP is invalidated

    # Rate limiting
    rate_limit_login_ip_max: int = 10  # Max login requests per IP per window
    rate_limit_login_email_max: int = 5  # Max login requests per email per window
    rate_limit_otp_ip_max: int = 10  # Max OTP verify requests per IP per window
    rate_limit_window_seconds: int = 900  # Rate limit window (15 minutes)

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
    function_container_image: str = "sinas-executor"  # Base image for execution (overridden by FUNCTION_CONTAINER_IMAGE env var)
    function_container_idle_timeout: int = 3600  # Seconds before idle container cleanup (1 hour)

    # Sandbox containers (isolated execution pool)
    sandbox_min_size: int = 4  # Containers to create on startup
    sandbox_max_size: int = 20  # Maximum sandbox containers
    sandbox_min_idle: int = 2  # Trigger replenish when idle drops below this
    sandbox_max_executions: int = 100  # Recycle container after this many executions
    sandbox_acquire_timeout: int = 30  # Seconds to wait for a container

    # Package management
    allow_package_installation: bool = True
    allowed_packages: Optional[str] = None  # Comma-separated whitelist, None = all allowed

    # Database pool
    db_pool_size: int = 20  # Connection pool size
    db_max_overflow: int = 30  # Max overflow connections beyond pool_size

    # Docker configuration
    backend_port: int = 8000  # Port the backend listens on (for file URLs on localhost)
    docker_network: str = "auto"  # Docker network for containers (auto-detect or specify)
    sandbox_network: str = "sinas-sandbox"  # Isolated network for executor containers (internet only, no access to internal services)
    default_worker_count: int = 4  # Number of workers to start on backend startup

    # Message history
    max_history_messages: int = 100  # Max messages to load for conversation history
    max_tool_iterations: int = 25  # Max consecutive tool-call rounds before stopping

    # Tool result store
    tool_result_retention_days: int = int(os.getenv("TOOL_RESULT_RETENTION_DAYS", "30"))
    tool_result_max_inline: int = int(os.getenv("TOOL_RESULT_MAX_INLINE", "5"))  # Last N results kept inline
    tool_result_max_size: int = int(os.getenv("TOOL_RESULT_MAX_SIZE", "102400"))  # 100KB truncation limit

    # Redis & Queue
    redis_url: str = "redis://redis:6379/0"
    queue_function_concurrency: int = 10
    queue_agent_concurrency: int = 5
    queue_default_timeout: int = 300
    queue_max_retries: int = 3
    queue_retry_delay: int = 10

    # Agent job settings
    agent_job_timeout: int = 600  # Default timeout for agent jobs (10 minutes)
    code_execution_timeout: int = 120  # Default timeout for code execution (2 minutes)

    # Encryption
    encryption_key: Optional[str] = None  # Fernet key for encrypting sensitive data

    # Superadmin
    superadmin_email: Optional[str] = None  # Email for superadmin user

    # Domain (for generating external URLs, e.g., temp file URLs)
    domain: Optional[str] = None  # FQDN like "app.example.com"; localhost or None = no external URLs

    # Component builder
    builder_url: str = "http://sinas-builder:3000"  # URL for esbuild compilation service

    # OpenTelemetry (opt-in observability — e.g. Langwatch)
    otel_enabled: bool = False
    otel_exporter_endpoint: Optional[str] = None  # e.g. https://app.langwatch.ai/api/otel/v1/traces
    otel_exporter_headers: Optional[str] = None  # e.g. X-Auth-Token=lw_xxx
    otel_service_name: str = "sinas"
    otel_vendor: str = "langwatch"  # Vendor preset: "langwatch", "langfuse", "generic_otel"

    # Declarative Configuration
    config_file: Optional[str] = None  # Path to YAML config file
    auto_apply_config: bool = False  # Auto-apply config file on startup

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"  # Ignore extra env vars like POSTGRES_PASSWORD


settings = Settings()
