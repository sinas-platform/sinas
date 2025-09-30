from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql://postgres:password@localhost:5432/maestro"
    
    # Redis
    redis_url: str = "redis://localhost:6379/0"
    
    # Application
    debug: bool = False
    secret_key: str = "your-secret-key-change-in-production"
    
    # Census Service Integration
    census_api_url: str = "http://localhost:8002"  # Census service URL
    census_jwt_secret: Optional[str] = None  # If provided, decode JWTs locally
    
    # Function execution
    function_timeout: int = 300  # 5 minutes
    max_function_memory: int = 512  # MB
    
    # Package management
    allow_package_installation: bool = True
    
    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()