"""File runtime endpoints - upload, download, list, delete."""
import base64
import json
import re
import uuid as uuid_lib
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.execution import TriggerType
from app.models.file import Collection, ContentFilterEvaluation, File, FileVersion
from app.models.function import Function
from app.schemas.file import (
    CollectionResponse,
    FileDownloadResponse,
    FileResponse,
    FileSearchRequest,
    FileSearchResult,
    FileUpload,
    FileVersionResponse,
    FileWithVersions,
)
from app.services.execution_engine import executor
from app.services.file_storage import FileStorage, get_storage

router = APIRouter()


@router.post("/{namespace}/{collection}", response_model=FileResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    namespace: str,
    collection: str,
    file_data: FileUpload,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """
    Upload a file to a collection.

    If the collection doesn't exist, it will be auto-created with defaults.
    If a file with the same name exists, a new version is created.
    """
    user_id, permissions = current_user_data
    storage: FileStorage = get_storage()

    # Check upload permission
    perm = f"sinas.collections/{namespace}/{collection}.upload:own"
    if not check_permission(permissions, perm):
        set_permission_used(http_request, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to upload files to this collection")
    set_permission_used(http_request, perm)

    # Get or create collection
    coll = await Collection.get_by_name(db, namespace, collection)
    if not coll:
        # Auto-create collection with defaults
        coll = Collection(
            namespace=namespace,
            name=collection,
            user_id=user_id,
        )
        db.add(coll)
        await db.commit()
        await db.refresh(coll)

    # Validate visibility setting
    if file_data.visibility == "shared" and not coll.allow_shared_files:
        raise HTTPException(status_code=400, detail="Shared files not allowed in this collection")
    if file_data.visibility == "private" and not coll.allow_private_files:
        raise HTTPException(status_code=400, detail="Private files not allowed in this collection")

    # Decode file content
    try:
        file_content = base64.b64decode(file_data.content_base64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid base64 content: {str(e)}")

    # Check file size
    file_size_mb = len(file_content) / (1024 * 1024)
    if file_size_mb > coll.max_file_size_mb:
        raise HTTPException(
            status_code=413,
            detail=f"File size {file_size_mb:.2f}MB exceeds collection limit {coll.max_file_size_mb}MB"
        )

    # Calculate hash
    file_hash = storage.calculate_hash(file_content)

    # Run content filter if configured
    approved_content = file_content
    approved_metadata = file_data.file_metadata
    filter_result = None

    if coll.content_filter_function:
        filter_namespace, filter_name = coll.content_filter_function.split("/")

        # Get function
        func = await Function.get_by_name(db, filter_namespace, filter_name)
        if not func:
            raise HTTPException(
                status_code=500,
                detail=f"Content filter function '{coll.content_filter_function}' not found"
            )

        # Execute filter
        filter_input = {
            "content_base64": file_data.content_base64,
            "namespace": namespace,
            "collection": collection,
            "filename": file_data.name,
            "content_type": file_data.content_type,
            "size_bytes": len(file_content),
            "user_metadata": file_data.file_metadata,
            "user_id": user_id,
        }

        # Generate execution ID for filter
        filter_execution_id = str(uuid_lib.uuid4())

        try:
            # Execute content filter function
            filter_result = await executor.execute_function(
                function_namespace=filter_namespace,
                function_name=filter_name,
                input_data=filter_input,
                execution_id=filter_execution_id,
                trigger_type=TriggerType.MANUAL.value,  # Content filters are manual/system triggers
                trigger_id=f"content_filter:{namespace}/{collection}",
                user_id=user_id,
            )

            # Validate result structure
            if not isinstance(filter_result, dict):
                raise HTTPException(
                    status_code=500,
                    detail="Content filter must return a dict with 'approved' field"
                )

            # Check if approved
            if not filter_result.get("approved", True):
                raise HTTPException(
                    status_code=400,
                    detail=f"Content filter rejected file: {filter_result.get('reason', 'No reason provided')}"
                )

            # Apply modifications if provided
            if filter_result.get("modified_content"):
                try:
                    approved_content = base64.b64decode(filter_result["modified_content"])
                    file_hash = storage.calculate_hash(approved_content)
                except Exception as e:
                    raise HTTPException(
                        status_code=500,
                        detail=f"Invalid modified_content from content filter: {str(e)}"
                    )

            # Merge filter metadata with user metadata
            if filter_result.get("metadata"):
                approved_metadata = {**file_data.file_metadata, **filter_result["metadata"]}

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Content filter execution failed: {str(e)}"
            )

    # Check if file name already exists (for this user if private, or anyone if shared is being checked)
    existing_query = select(File).where(
        and_(
            File.collection_id == coll.id,
            File.name == file_data.name
        )
    )

    # Apply uniqueness rule: can't create file with name you can see
    if file_data.visibility == "private":
        # Check if user has a private file OR if shared file exists
        existing_query = existing_query.where(
            or_(
                File.user_id == user_id,
                File.visibility == "shared"
            )
        )
    else:
        # Check if shared file exists (anyone's)
        existing_query = existing_query.where(File.visibility == "shared")

    result = await db.execute(existing_query)
    existing_file = result.scalar_one_or_none()

    if existing_file:
        # Update existing file with new version
        file_record = existing_file
        file_record.current_version += 1
        file_record.content_type = file_data.content_type
        file_record.file_metadata = approved_metadata
    else:
        # Create new file
        file_record = File(
            collection_id=coll.id,
            name=file_data.name,
            user_id=user_id,
            content_type=file_data.content_type,
            current_version=1,
            file_metadata=approved_metadata,
            visibility=file_data.visibility,
        )
        db.add(file_record)

    await db.commit()
    await db.refresh(file_record)

    # Store file content
    storage_path = f"{namespace}/{collection}/{file_record.id}/v{file_record.current_version}"
    await storage.save(storage_path, approved_content)

    # Create version record
    version = FileVersion(
        file_id=file_record.id,
        version_number=file_record.current_version,
        storage_path=storage_path,
        size_bytes=len(approved_content),
        hash_sha256=file_hash,
        uploaded_by=user_id,
    )
    db.add(version)

    # Save content filter evaluation if filter was run
    if coll.content_filter_function and filter_result:
        evaluation = ContentFilterEvaluation(
            file_id=file_record.id,
            version_number=file_record.current_version,
            function_namespace=filter_namespace,
            function_name=filter_name,
            result=filter_result,
        )
        db.add(evaluation)

    await db.commit()

    # Trigger post-upload function if configured (async, don't block)
    if coll.post_upload_function:
        post_namespace, post_name = coll.post_upload_function.split("/")

        # Get post-upload function
        post_func = await Function.get_by_name(db, post_namespace, post_name)

        if post_func:
            post_input = {
                "file_id": str(file_record.id),
                "namespace": namespace,
                "collection": collection,
                "filename": file_data.name,
                "version": file_record.current_version,
                "file_path": storage_path,
                "user_id": user_id,
                "metadata": approved_metadata,
            }

            # Generate execution ID for post-upload
            post_execution_id = str(uuid_lib.uuid4())

            try:
                # Execute post-upload function in background (don't await)
                import asyncio
                asyncio.create_task(
                    executor.execute_function(
                        function_namespace=post_namespace,
                        function_name=post_name,
                        input_data=post_input,
                        execution_id=post_execution_id,
                        trigger_type=TriggerType.MANUAL.value,
                        trigger_id=f"post_upload:{namespace}/{collection}",
                        user_id=user_id,
                    )
                )
            except Exception:
                # Don't fail upload if post-upload trigger fails
                pass

    return FileResponse.model_validate(file_record)


@router.get("/{namespace}/{collection}/{filename}", response_model=FileDownloadResponse)
async def download_file(
    namespace: str,
    collection: str,
    filename: str,
    version: Optional[int] = None,
    http_request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Download a file from a collection."""
    user_id, permissions = current_user_data
    storage: FileStorage = get_storage()

    # Check download permission
    perm = f"sinas.collections/{namespace}/{collection}.download:own"
    if not check_permission(permissions, perm):
        set_permission_used(http_request, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to download files from this collection")
    set_permission_used(http_request, perm)

    # Get collection
    coll = await Collection.get_by_name(db, namespace, collection)
    if not coll:
        raise HTTPException(status_code=404, detail="Collection not found")

    # Get file
    result = await db.execute(
        select(File).where(
            and_(
                File.collection_id == coll.id,
                File.name == filename
            )
        )
    )
    file_record = result.scalar_one_or_none()

    if not file_record:
        raise HTTPException(status_code=404, detail="File not found")

    # Check visibility
    has_all_perm = check_permission(permissions, f"sinas.collections/{namespace}/{collection}.download:all")
    if file_record.visibility == "private" and str(file_record.user_id) != user_id and not has_all_perm:
        raise HTTPException(status_code=403, detail="Not authorized to access this private file")

    # Get version
    version_number = version or file_record.current_version
    result = await db.execute(
        select(FileVersion).where(
            and_(
                FileVersion.file_id == file_record.id,
                FileVersion.version_number == version_number
            )
        )
    )
    file_version = result.scalar_one_or_none()

    if not file_version:
        raise HTTPException(status_code=404, detail=f"Version {version_number} not found")

    # Read file content
    try:
        file_content = await storage.read(file_version.storage_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File content not found in storage")

    return FileDownloadResponse(
        content_base64=base64.b64encode(file_content).decode("utf-8"),
        content_type=file_record.content_type,
        file_metadata=file_record.file_metadata,
        version=version_number,
    )


@router.get("/{namespace}/{collection}", response_model=list[FileWithVersions])
async def list_files(
    namespace: str,
    collection: str,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """List all files in a collection."""
    user_id, permissions = current_user_data

    # Check list permission
    perm = f"sinas.collections/{namespace}/{collection}.list:own"
    if not check_permission(permissions, perm):
        set_permission_used(http_request, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to list files in this collection")
    set_permission_used(http_request, perm)

    # Get collection
    coll = await Collection.get_by_name(db, namespace, collection)
    if not coll:
        raise HTTPException(status_code=404, detail="Collection not found")

    # Get files
    has_all_perm = check_permission(permissions, f"sinas.collections/{namespace}/{collection}.list:all")

    query = select(File).where(File.collection_id == coll.id)

    if not has_all_perm:
        # Only show user's private files + all shared files
        query = query.where(
            or_(
                File.user_id == user_id,
                File.visibility == "shared"
            )
        )

    query = query.order_by(File.name)

    result = await db.execute(query)
    files = result.scalars().all()

    # Load versions for each file
    responses = []
    for file_record in files:
        result = await db.execute(
            select(FileVersion)
            .where(FileVersion.file_id == file_record.id)
            .order_by(FileVersion.version_number.desc())
        )
        versions = result.scalars().all()

        responses.append(FileWithVersions(
            **FileResponse.model_validate(file_record).model_dump(),
            versions=[FileVersionResponse.model_validate(v) for v in versions]
        ))

    return responses


@router.delete("/{namespace}/{collection}/{filename}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    namespace: str,
    collection: str,
    filename: str,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Delete a file and all its versions."""
    user_id, permissions = current_user_data
    storage: FileStorage = get_storage()

    # Check delete permission
    perm = f"sinas.collections/{namespace}/{collection}.delete_files:own"
    if not check_permission(permissions, perm):
        set_permission_used(http_request, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to delete files from this collection")
    set_permission_used(http_request, perm)

    # Get collection
    coll = await Collection.get_by_name(db, namespace, collection)
    if not coll:
        raise HTTPException(status_code=404, detail="Collection not found")

    # Get file
    result = await db.execute(
        select(File).where(
            and_(
                File.collection_id == coll.id,
                File.name == filename
            )
        )
    )
    file_record = result.scalar_one_or_none()

    if not file_record:
        raise HTTPException(status_code=404, detail="File not found")

    # Check ownership for private files
    has_all_perm = check_permission(permissions, f"sinas.collections/{namespace}/{collection}.delete_files:all")
    if file_record.visibility == "private" and str(file_record.user_id) != user_id and not has_all_perm:
        raise HTTPException(status_code=403, detail="Not authorized to delete this private file")

    # Delete physical files
    result = await db.execute(
        select(FileVersion).where(FileVersion.file_id == file_record.id)
    )
    versions = result.scalars().all()

    for version in versions:
        try:
            await storage.delete(version.storage_path)
        except Exception:
            # Continue even if storage deletion fails
            pass

    # Delete database record (cascade will delete versions and evaluations)
    await db.delete(file_record)
    await db.commit()

    return None
