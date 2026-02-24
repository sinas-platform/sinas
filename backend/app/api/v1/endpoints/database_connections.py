"""Database connection endpoints for managing external database configurations."""
import time
import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_permission
from app.core.database import get_db
from app.core.encryption import EncryptionService
from app.models.database_connection import DatabaseConnection
from app.schemas.database_connection import (
    DatabaseConnectionCreate,
    DatabaseConnectionResponse,
    DatabaseConnectionTestRequest,
    DatabaseConnectionTestResponse,
    DatabaseConnectionUpdate,
)

router = APIRouter()


@router.post("", response_model=DatabaseConnectionResponse, status_code=status.HTTP_201_CREATED)
async def create_database_connection(
    request: DatabaseConnectionCreate,
    user_id: str = Depends(require_permission("sinas.database_connections.create:all")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new database connection. Admin only."""
    result = await db.execute(
        select(DatabaseConnection).where(DatabaseConnection.name == request.name)
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Database connection with name '{request.name}' already exists",
        )

    encrypted_password = None
    if request.password:
        encryption_service = EncryptionService()
        encrypted_password = encryption_service.encrypt(request.password)

    connection = DatabaseConnection(
        name=request.name,
        connection_type=request.connection_type,
        host=request.host,
        port=request.port,
        database=request.database,
        username=request.username,
        password=encrypted_password,
        ssl_mode=request.ssl_mode,
        config=request.config or {},
        is_active=True,
    )

    db.add(connection)
    await db.commit()
    await db.refresh(connection)

    return DatabaseConnectionResponse.model_validate(connection)


@router.get("", response_model=list[DatabaseConnectionResponse])
async def list_database_connections(
    user_id: str = Depends(require_permission("sinas.database_connections.read:all")),
    db: AsyncSession = Depends(get_db),
):
    """List all database connections. Admin only."""
    result = await db.execute(
        select(DatabaseConnection).order_by(DatabaseConnection.created_at.desc())
    )
    connections = result.scalars().all()
    return [DatabaseConnectionResponse.model_validate(c) for c in connections]


@router.get("/{name}", response_model=DatabaseConnectionResponse)
async def get_database_connection(
    name: str,
    user_id: str = Depends(require_permission("sinas.database_connections.read:all")),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific database connection by name. Admin only."""
    connection = await DatabaseConnection.get_by_name(db, name)
    if not connection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Database connection '{name}' not found",
        )
    return DatabaseConnectionResponse.model_validate(connection)


@router.patch("/{connection_id}", response_model=DatabaseConnectionResponse)
async def update_database_connection(
    connection_id: uuid.UUID,
    request: DatabaseConnectionUpdate,
    user_id: str = Depends(require_permission("sinas.database_connections.update:all")),
    db: AsyncSession = Depends(get_db),
):
    """Update a database connection. Admin only."""
    result = await db.execute(
        select(DatabaseConnection).where(DatabaseConnection.id == connection_id)
    )
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Database connection '{connection_id}' not found",
        )

    if request.name is not None:
        name_check = await db.execute(
            select(DatabaseConnection).where(
                DatabaseConnection.name == request.name,
                DatabaseConnection.id != connection.id,
            )
        )
        if name_check.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Database connection with name '{request.name}' already exists",
            )
        connection.name = request.name

    if request.connection_type is not None:
        connection.connection_type = request.connection_type
    if request.host is not None:
        connection.host = request.host
    if request.port is not None:
        connection.port = request.port
    if request.database is not None:
        connection.database = request.database
    if request.username is not None:
        connection.username = request.username
    if request.password is not None:
        encryption_service = EncryptionService()
        connection.password = encryption_service.encrypt(request.password)
    if request.ssl_mode is not None:
        connection.ssl_mode = request.ssl_mode
    if request.config is not None:
        connection.config = request.config
    if request.is_active is not None:
        connection.is_active = request.is_active

    await db.commit()
    await db.refresh(connection)

    # Invalidate pool on config change
    from app.services.database_pool import DatabasePoolManager

    await DatabasePoolManager.get_instance().invalidate(str(connection_id))

    return DatabaseConnectionResponse.model_validate(connection)


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_database_connection(
    connection_id: uuid.UUID,
    user_id: str = Depends(require_permission("sinas.database_connections.delete:all")),
    db: AsyncSession = Depends(get_db),
):
    """Soft delete a database connection. Admin only."""
    result = await db.execute(
        select(DatabaseConnection).where(DatabaseConnection.id == connection_id)
    )
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Database connection '{connection_id}' not found",
        )

    connection.is_active = False
    await db.commit()

    # Invalidate pool
    from app.services.database_pool import DatabasePoolManager

    await DatabasePoolManager.get_instance().invalidate(str(connection_id))


@router.post("/test", response_model=DatabaseConnectionTestResponse)
async def test_database_connection_raw(
    request: DatabaseConnectionTestRequest,
    user_id: str = Depends(require_permission("sinas.database_connections.read:all")),
):
    """Test a database connection with raw parameters (before saving). Admin only."""
    start_time = time.time()
    try:
        if request.connection_type == "postgresql":
            ssl_mode = request.ssl_mode or "prefer"
            conn = await asyncpg.connect(
                host=request.host,
                port=request.port,
                database=request.database,
                user=request.username,
                password=request.password,
                ssl=ssl_mode,
                timeout=10,
            )
            await conn.execute("SELECT 1")
            await conn.close()
        else:
            return DatabaseConnectionTestResponse(
                success=False,
                message=f"Connection type '{request.connection_type}' testing not yet supported",
            )

        latency_ms = int((time.time() - start_time) * 1000)
        return DatabaseConnectionTestResponse(
            success=True,
            message="Connection successful",
            latency_ms=latency_ms,
        )
    except Exception as e:
        latency_ms = int((time.time() - start_time) * 1000)
        return DatabaseConnectionTestResponse(
            success=False,
            message=f"Connection failed: {str(e)}",
            latency_ms=latency_ms,
        )


@router.post("/{connection_id}/test", response_model=DatabaseConnectionTestResponse)
async def test_database_connection(
    connection_id: uuid.UUID,
    user_id: str = Depends(require_permission("sinas.database_connections.read:all")),
    db: AsyncSession = Depends(get_db),
):
    """Test a database connection. Admin only."""
    result = await db.execute(
        select(DatabaseConnection).where(DatabaseConnection.id == connection_id)
    )
    connection = result.scalar_one_or_none()
    if not connection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Database connection '{connection_id}' not found",
        )

    # Decrypt password
    decrypted_password = None
    if connection.password:
        encryption_service = EncryptionService()
        decrypted_password = encryption_service.decrypt(connection.password)

    start_time = time.time()
    try:
        if connection.connection_type == "postgresql":
            ssl_mode = connection.ssl_mode or "prefer"
            conn = await asyncpg.connect(
                host=connection.host,
                port=connection.port,
                database=connection.database,
                user=connection.username,
                password=decrypted_password,
                ssl=ssl_mode,
                timeout=10,
            )
            await conn.execute("SELECT 1")
            await conn.close()
        else:
            return DatabaseConnectionTestResponse(
                success=False,
                message=f"Connection type '{connection.connection_type}' testing not yet supported",
            )

        latency_ms = int((time.time() - start_time) * 1000)
        return DatabaseConnectionTestResponse(
            success=True,
            message="Connection successful",
            latency_ms=latency_ms,
        )
    except Exception as e:
        latency_ms = int((time.time() - start_time) * 1000)
        return DatabaseConnectionTestResponse(
            success=False,
            message=f"Connection failed: {str(e)}",
            latency_ms=latency_ms,
        )
