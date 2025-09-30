#!/usr/bin/env python3

import asyncio
import sys
import os

# Add the project root to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alembic.config import Config
from alembic import command
from app.core.database import async_engine
from app.models.base import Base


async def create_database():
    """Create all database tables."""
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Database tables created successfully!")


def run_migrations():
    """Run Alembic migrations."""
    alembic_cfg = Config("alembic.ini")
    
    # Create initial migration if none exist
    try:
        command.revision(alembic_cfg, autogenerate=True, message="Initial migration")
        print("Initial migration created")
    except Exception as e:
        print(f"Migration creation info: {e}")
    
    # Run migrations
    try:
        command.upgrade(alembic_cfg, "head")
        print("Migrations applied successfully!")
    except Exception as e:
        print(f"Migration error: {e}")


async def main():
    print("Setting up Maestro database...")
    
    # Create tables directly (for development)
    await create_database()
    
    # Run Alembic migrations (for production)
    run_migrations()
    
    print("Database setup complete!")


if __name__ == "__main__":
    asyncio.run(main())