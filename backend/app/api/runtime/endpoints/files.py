"""File runtime endpoints - upload, download, list, delete, search."""
import asyncio
import base64
import logging
import re
import uuid as uuid_lib
from typing import Optional

import jsonschema
from jose import JWTError, jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.config import settings
from app.core.database import get_db
from app.core.permissions import check_permission
from app.models.execution import TriggerType
from app.models.file import Collection, ContentFilterEvaluation, File, FileVersion
from app.models.function import Function
from app.schemas.file import (
    CollectionResponse,
    FileDownloadResponse,
    FileMetadataUpdate,
    FileResponse,
    FileSearchMatch,
    FileSearchRequest,
    FileSearchResult,
    FileUpload,
    FileVersionResponse,
    FileWithVersions,
)
from app.services.execution_engine import executor
from app.services.file_storage import FileStorage, get_storage

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/serve/{token}")
async def serve_file(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Serve a file via a signed JWT token (unauthenticated).

    The token contains the file_id, version, and expiry. No auth header needed.
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired file token")

    if payload.get("purpose") != "file_serve":
        raise HTTPException(status_code=401, detail="Invalid token purpose")

    file_id = payload.get("file_id")
    version = payload.get("version")
    if not file_id or version is None:
        raise HTTPException(status_code=400, detail="Invalid token payload")

    # Look up file
    result = await db.execute(select(File).where(File.id == file_id))
    file_record = result.scalar_one_or_none()
    if not file_record:
        raise HTTPException(status_code=404, detail="File not found")

    # Look up version
    result = await db.execute(
        select(FileVersion).where(
            and_(
                FileVersion.file_id == file_record.id,
                FileVersion.version_number == version,
            )
        )
    )
    file_version = result.scalar_one_or_none()
    if not file_version:
        raise HTTPException(status_code=404, detail="File version not found")

    # Read content
    storage: FileStorage = get_storage()
    try:
        content = await storage.read(file_version.storage_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File content not found in storage")

    return Response(
        content=content,
        media_type=file_record.content_type,
        headers={"Content-Disposition": f'inline; filename="{file_record.name}"'},
    )


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

    # Validate file metadata against collection schema
    if coll.metadata_schema:
        try:
            jsonschema.validate(instance=file_data.file_metadata, schema=coll.metadata_schema)
        except jsonschema.ValidationError as e:
            raise HTTPException(
                status_code=400,
                detail=f"File metadata validation failed: {e.message}"
            )

    # Decode file content
    try:
        file_content = base64.b64decode(file_data.content_base64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid base64 content: {str(e)}")

    # Check file size
    file_size_bytes = len(file_content)
    file_size_mb = file_size_bytes / (1024 * 1024)
    if file_size_mb > coll.max_file_size_mb:
        raise HTTPException(
            status_code=413,
            detail=f"File size {file_size_mb:.2f}MB exceeds collection limit {coll.max_file_size_mb}MB"
        )

    # Check total storage quota
    total_size_result = await db.execute(
        select(func.coalesce(func.sum(FileVersion.size_bytes), 0))
        .join(File, FileVersion.file_id == File.id)
        .where(File.collection_id == coll.id)
    )
    current_total_bytes = total_size_result.scalar()
    max_total_bytes = coll.max_total_size_gb * 1024 * 1024 * 1024
    if current_total_bytes + file_size_bytes > max_total_bytes:
        current_gb = current_total_bytes / (1024 * 1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"Collection storage quota exceeded ({current_gb:.2f}GB / {coll.max_total_size_gb}GB)"
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
        func_record = await Function.get_by_name(db, filter_namespace, filter_name)
        if not func_record:
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
                trigger_type=TriggerType.MANUAL.value,
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

    # Check if file name already exists, using FOR UPDATE to prevent race conditions
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

    # Lock the row to prevent concurrent modifications
    existing_query = existing_query.with_for_update()

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

    # Flush to get file_record.id without committing
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Concurrent upload conflict for file '{file_data.name}'"
        )

    # Determine storage path (always unique per version)
    storage_path = f"{namespace}/{collection}/{file_record.id}/v{file_record.current_version}"

    # Save to storage FIRST, then commit DB (prevents orphan DB records)
    try:
        await storage.save(storage_path, approved_content)
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save file to storage: {str(e)}"
        )

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

    try:
        await db.commit()
        await db.refresh(file_record)
    except Exception as e:
        # Clean up storage if DB commit fails
        try:
            await storage.delete(storage_path)
        except Exception:
            logger.warning(f"Failed to clean up storage at {storage_path} after DB error")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save file record: {str(e)}"
        )

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

            post_execution_id = str(uuid_lib.uuid4())

            try:
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

    return FileResponse(
        id=file_record.id,
        namespace=namespace,
        name=file_record.name,
        user_id=file_record.user_id,
        content_type=file_record.content_type,
        current_version=file_record.current_version,
        file_metadata=file_record.file_metadata,
        visibility=file_record.visibility,
        created_at=file_record.created_at,
        updated_at=file_record.updated_at,
    )


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


@router.post("/{namespace}/{collection}/{filename}/url")
async def generate_temp_url(
    namespace: str,
    collection: str,
    filename: str,
    version: Optional[int] = None,
    expires_in: int = 3600,
    http_request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """
    Generate a temporary public URL for a file.

    The URL is signed with a JWT and does not require authentication to access.
    Returns a data URL (base64) if DOMAIN is not configured (localhost dev).
    """
    user_id, permissions = current_user_data

    # Reuse download permission
    perm = f"sinas.collections/{namespace}/{collection}.download:own"
    if not check_permission(permissions, perm):
        set_permission_used(http_request, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to access files in this collection")
    set_permission_used(http_request, perm)

    # Clamp expires_in to reasonable bounds
    expires_in = max(60, min(expires_in, 86400))  # 1 min to 24 hours

    # Get collection
    coll = await Collection.get_by_name(db, namespace, collection)
    if not coll:
        raise HTTPException(status_code=404, detail="Collection not found")

    # Get file
    result = await db.execute(
        select(File).where(
            and_(
                File.collection_id == coll.id,
                File.name == filename,
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

    # Determine version
    version_number = version or file_record.current_version

    # Verify version exists
    ver_result = await db.execute(
        select(FileVersion).where(
            and_(
                FileVersion.file_id == file_record.id,
                FileVersion.version_number == version_number,
            )
        )
    )
    file_version = ver_result.scalar_one_or_none()
    if not file_version:
        raise HTTPException(status_code=404, detail=f"Version {version_number} not found")

    # Generate URL
    from app.services.file_storage import generate_file_data_url, generate_file_url

    url = generate_file_url(str(file_record.id), version_number, expires_in=expires_in)
    if not url:
        # Fallback to data URL for localhost
        url = generate_file_data_url(file_version.storage_path, file_record.content_type)

    return {
        "url": url,
        "filename": file_record.name,
        "content_type": file_record.content_type,
        "version": version_number,
        "expires_in": expires_in,
    }


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
            id=file_record.id,
            namespace=namespace,
            name=file_record.name,
            user_id=file_record.user_id,
            content_type=file_record.content_type,
            current_version=file_record.current_version,
            file_metadata=file_record.file_metadata,
            visibility=file_record.visibility,
            created_at=file_record.created_at,
            updated_at=file_record.updated_at,
            versions=[FileVersionResponse.model_validate(v) for v in versions]
        ))

    return responses


@router.post("/{namespace}/{collection}/search", response_model=list[FileSearchResult])
async def search_files(
    namespace: str,
    collection: str,
    search_request: FileSearchRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Search files in a collection by metadata and/or content."""
    user_id, permissions = current_user_data
    storage: FileStorage = get_storage()

    # Reuse list permission for search
    perm = f"sinas.collections/{namespace}/{collection}.list:own"
    if not check_permission(permissions, perm):
        set_permission_used(http_request, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to search files in this collection")
    set_permission_used(http_request, perm)

    # Get collection
    coll = await Collection.get_by_name(db, namespace, collection)
    if not coll:
        raise HTTPException(status_code=404, detail="Collection not found")

    # Build base query with visibility rules
    has_all_perm = check_permission(permissions, f"sinas.collections/{namespace}/{collection}.list:all")
    query = select(File).where(File.collection_id == coll.id)

    if not has_all_perm:
        query = query.where(
            or_(
                File.user_id == user_id,
                File.visibility == "shared"
            )
        )

    # Apply metadata filters using PostgreSQL JSON containment
    if search_request.metadata_filter:
        for key, value in search_request.metadata_filter.items():
            query = query.where(File.file_metadata[key].as_string() == str(value))

    query = query.order_by(File.name).limit(search_request.limit)

    result = await db.execute(query)
    files = result.scalars().all()

    results = []

    # If no text query, return files matching metadata filter
    if not search_request.query:
        for file_record in files:
            results.append(FileSearchResult(
                file_id=file_record.id,
                filename=file_record.name,
                version=file_record.current_version,
                matches=[],
            ))
        return results

    # Compile regex pattern
    try:
        pattern = re.compile(search_request.query)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regex pattern: {str(e)}")

    # Known binary content types to skip
    BINARY_PREFIXES = ("image/", "audio/", "video/", "font/")
    BINARY_TYPES = {
        "application/pdf", "application/zip", "application/gzip",
        "application/x-tar", "application/x-bzip2", "application/x-7z-compressed",
        "application/vnd.openxmlformats", "application/msword",
        "application/vnd.ms-excel", "application/vnd.ms-powerpoint",
    }

    # Search file content
    for file_record in files:
        # Skip known binary types
        ct = file_record.content_type
        if any(ct.startswith(p) for p in BINARY_PREFIXES) or ct in BINARY_TYPES:
            continue

        # Get current version
        ver_result = await db.execute(
            select(FileVersion).where(
                and_(
                    FileVersion.file_id == file_record.id,
                    FileVersion.version_number == file_record.current_version
                )
            )
        )
        file_version = ver_result.scalar_one_or_none()
        if not file_version:
            continue

        # Read file content — try to decode as text, skip if binary
        try:
            content = await storage.read(file_version.storage_path)
            text = content.decode("utf-8")
        except (Exception, UnicodeDecodeError):
            continue

        lines = text.split("\n")
        matches = []

        for i, line in enumerate(lines):
            if pattern.search(line):
                # Build context (2 lines before and after)
                context_start = max(0, i - 2)
                context_end = min(len(lines), i + 3)
                context = lines[context_start:context_end]

                matches.append(FileSearchMatch(
                    line=i + 1,
                    text=line,
                    context=context,
                ))

        if matches:
            results.append(FileSearchResult(
                file_id=file_record.id,
                filename=file_record.name,
                version=file_record.current_version,
                matches=matches,
            ))

    return results


@router.patch("/{namespace}/{collection}/{filename}", response_model=FileResponse)
async def update_file_metadata(
    namespace: str,
    collection: str,
    filename: str,
    update_data: FileMetadataUpdate,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Update file metadata."""
    user_id, permissions = current_user_data

    # Reuse upload permission for metadata edits
    perm = f"sinas.collections/{namespace}/{collection}.upload:own"
    if not check_permission(permissions, perm):
        set_permission_used(http_request, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to update files in this collection")
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

    # Check ownership
    has_all_perm = check_permission(permissions, f"sinas.collections/{namespace}/{collection}.upload:all")
    if str(file_record.user_id) != user_id and not has_all_perm:
        raise HTTPException(status_code=403, detail="Not authorized to update this file")

    # Validate metadata against collection schema
    if coll.metadata_schema:
        try:
            jsonschema.validate(instance=update_data.file_metadata, schema=coll.metadata_schema)
        except jsonschema.ValidationError as e:
            raise HTTPException(
                status_code=400,
                detail=f"File metadata validation failed: {e.message}"
            )

    file_record.file_metadata = update_data.file_metadata
    await db.commit()
    await db.refresh(file_record)

    return FileResponse(
        id=file_record.id,
        namespace=namespace,
        name=file_record.name,
        user_id=file_record.user_id,
        content_type=file_record.content_type,
        current_version=file_record.current_version,
        file_metadata=file_record.file_metadata,
        visibility=file_record.visibility,
        created_at=file_record.created_at,
        updated_at=file_record.updated_at,
    )


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

    # Get all versions of this file
    result = await db.execute(
        select(FileVersion).where(FileVersion.file_id == file_record.id)
    )
    versions = result.scalars().all()

    # Delete physical files
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
