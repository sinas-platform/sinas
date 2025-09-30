from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import List, Optional
from datetime import datetime

from app.core.database import get_db
from app.core.auth import get_subtenant_context
from app.models.function import Function, FunctionVersion
from app.api.schemas import (
    FunctionCreate, 
    FunctionUpdate, 
    FunctionResponse, 
    FunctionVersionResponse
)
from app.services.execution_engine import executor

router = APIRouter(prefix="/functions", tags=["functions"])


@router.post("", response_model=FunctionResponse)
async def create_function(
    function_data: FunctionCreate,
    db: AsyncSession = Depends(get_db),
    subtenant_id: str = Depends(get_subtenant_context())
):
    # Check if function name already exists within subtenant
    existing = await db.execute(
        select(Function).where(
            and_(
                Function.subtenant_id == subtenant_id,
                Function.name == function_data.name
            )
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Function with this name already exists")
    
    # Create function
    function = Function(
        subtenant_id=subtenant_id,
        name=function_data.name,
        description=function_data.description,
        code=function_data.code,
        input_schema=function_data.input_schema,
        output_schema=function_data.output_schema,
        requirements=function_data.requirements,
        tags=function_data.tags
    )
    
    db.add(function)
    await db.commit()
    await db.refresh(function)
    
    # Create initial version
    version = FunctionVersion(
        subtenant_id=subtenant_id,
        function_id=function.id,
        version=1,
        code=function_data.code,
        input_schema=function_data.input_schema,
        output_schema=function_data.output_schema,
        created_by="system"  # TODO: Add user authentication
    )
    
    db.add(version)
    await db.commit()
    
    # Clear executor cache
    executor.clear_cache()
    
    return function


@router.get("", response_model=List[FunctionResponse])
async def list_functions(
    tags: Optional[List[str]] = Query(None),
    active_only: bool = True,
    db: AsyncSession = Depends(get_db),
    subtenant_id: str = Depends(get_subtenant_context())
):
    query = select(Function).where(Function.subtenant_id == subtenant_id)
    
    if active_only:
        query = query.where(Function.is_active == True)
    
    if tags:
        # Filter by tags (function has any of the specified tags)
        for tag in tags:
            query = query.where(Function.tags.contains([tag]))
    
    result = await db.execute(query.order_by(Function.name))
    functions = result.scalars().all()
    
    return functions


@router.get("/{name}", response_model=FunctionResponse)
async def get_function(
    name: str, 
    db: AsyncSession = Depends(get_db),
    subtenant_id: str = Depends(get_subtenant_context())
):
    result = await db.execute(
        select(Function).where(
            and_(
                Function.subtenant_id == subtenant_id,
                Function.name == name
            )
        )
    )
    function = result.scalar_one_or_none()
    
    if not function:
        raise HTTPException(status_code=404, detail="Function not found")
    
    return function


@router.put("/{name}", response_model=FunctionResponse)
async def update_function(
    name: str,
    function_data: FunctionUpdate,
    db: AsyncSession = Depends(get_db),
    subtenant_id: str = Depends(get_subtenant_context())
):
    result = await db.execute(
        select(Function).where(
            and_(
                Function.subtenant_id == subtenant_id,
                Function.name == name
            )
        )
    )
    function = result.scalar_one_or_none()
    
    if not function:
        raise HTTPException(status_code=404, detail="Function not found")
    
    # Store current version if code is being updated
    create_version = False
    if function_data.code and function_data.code != function.code:
        create_version = True
        
        # Get next version number
        version_result = await db.execute(
            select(FunctionVersion.version)
            .where(FunctionVersion.function_id == function.id)
            .order_by(FunctionVersion.version.desc())
            .limit(1)
        )
        latest_version = version_result.scalar_one_or_none() or 0
        next_version = latest_version + 1
    
    # Update function fields
    update_data = function_data.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(function, field, value)
    
    function.updated_at = datetime.utcnow()
    
    # Create new version if code changed
    if create_version:
        version = FunctionVersion(
            subtenant_id=subtenant_id,
            function_id=function.id,
            version=next_version,
            code=function_data.code,
            input_schema=function_data.input_schema or function.input_schema,
            output_schema=function_data.output_schema or function.output_schema,
            created_by="system"  # TODO: Add user authentication
        )
        db.add(version)
    
    await db.commit()
    await db.refresh(function)
    
    # Clear executor cache
    executor.clear_cache()
    
    return function


@router.delete("/{name}")
async def delete_function(
    name: str, 
    db: AsyncSession = Depends(get_db),
    subtenant_id: str = Depends(get_subtenant_context())
):
    result = await db.execute(
        select(Function).where(Function.name == name)
    )
    function = result.scalar_one_or_none()
    
    if not function:
        raise HTTPException(status_code=404, detail="Function not found")
    
    # Soft delete by setting is_active to False
    function.is_active = False
    function.updated_at = datetime.utcnow()
    
    await db.commit()
    
    # Clear executor cache
    executor.clear_cache()
    
    return {"message": "Function deleted successfully"}


@router.get("/{name}/versions", response_model=List[FunctionVersionResponse])
async def get_function_versions(
    name: str, 
    db: AsyncSession = Depends(get_db),
    subtenant_id: str = Depends(get_subtenant_context())
):
    # First get the function
    result = await db.execute(
        select(Function).where(Function.name == name)
    )
    function = result.scalar_one_or_none()
    
    if not function:
        raise HTTPException(status_code=404, detail="Function not found")
    
    # Get all versions
    versions_result = await db.execute(
        select(FunctionVersion)
        .where(FunctionVersion.function_id == function.id)
        .order_by(FunctionVersion.version.desc())
    )
    versions = versions_result.scalars().all()
    
    return versions


@router.post("/{name}/rollback/{version}", response_model=FunctionResponse)
async def rollback_function(
    name: str, 
    version: int, 
    db: AsyncSession = Depends(get_db)
):
    # Get function
    result = await db.execute(
        select(Function).where(Function.name == name)
    )
    function = result.scalar_one_or_none()
    
    if not function:
        raise HTTPException(status_code=404, detail="Function not found")
    
    # Get specific version
    version_result = await db.execute(
        select(FunctionVersion).where(
            and_(
                FunctionVersion.function_id == function.id,
                FunctionVersion.version == version
            )
        )
    )
    target_version = version_result.scalar_one_or_none()
    
    if not target_version:
        raise HTTPException(status_code=404, detail="Version not found")
    
    # Update function to match the target version
    function.code = target_version.code
    function.input_schema = target_version.input_schema
    function.output_schema = target_version.output_schema
    function.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(function)
    
    # Clear executor cache
    executor.clear_cache()
    
    return function


