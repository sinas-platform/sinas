"""
Declarative configuration endpoints
Handles applying, validating, and exporting SINAS configuration
"""
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.schemas.config import (
    ConfigApplyRequest,
    ConfigApplyResponse,
    ConfigValidateRequest,
    ConfigValidateResponse,
    ValidationError as SchemaValidationError,
)
from app.models.config_revision import ConfigRevision
from app.schemas.config_history import ConfigRevisionResponse
from app.services.config_apply import ConfigApplyService
from app.services.config_export import ConfigExportService
from app.services.config_parser import ConfigParser

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/validate", response_model=ConfigValidateResponse)
async def validate_config(
    request: Request,
    validate_request: ConfigValidateRequest,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """
    Validate YAML configuration without applying

    Checks:
    - YAML syntax
    - Schema validation
    - Reference validation (checks database for existing resources)
    - Environment variables
    """
    user_id, permissions = current_user_data

    # Check permission
    perm = "sinas.config.validate:all"
    if not check_permission(permissions, perm):
        set_permission_used(request, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to validate config")

    set_permission_used(request, perm, has_perm=True)

    # Parse and validate
    config, validation = await ConfigParser.parse_and_validate(
        validate_request.config,
        db=db,  # Pass database for checking existing resources
        strict=False,  # Don't error on missing env vars for validation
    )

    # Convert ConfigValidation to ConfigValidateResponse
    return ConfigValidateResponse(
        valid=validation.is_valid,
        errors=[SchemaValidationError(path=e.path, message=e.message) for e in validation.errors],
        warnings=[SchemaValidationError(path="", message=w) for w in validation.warnings],
    )


@router.post("/apply", response_model=ConfigApplyResponse)
async def apply_config(
    request: Request,
    apply_request: ConfigApplyRequest,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """
    Apply YAML configuration idempotently

    Features:
    - Creates new resources
    - Updates existing config-managed resources
    - Skips non-config-managed resources
    - Dry run support
    - Atomic transactions (rollback on error)
    """
    user_id, permissions = current_user_data

    # Check permission
    perm = "sinas.config.apply:all"
    if not check_permission(permissions, perm):
        set_permission_used(request, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to apply config")

    set_permission_used(request, perm, has_perm=True)

    try:
        # Parse and validate (with database-aware checking)
        config, validation = await ConfigParser.parse_and_validate(
            apply_request.config,
            db=db,  # Pass database for checking existing resources
            strict=not apply_request.force,  # Allow missing env vars if force=True
        )

        if not validation.is_valid and not apply_request.force:
            # Return validation errors without applying
            return ConfigApplyResponse(
                success=False,
                summary={},
                changes=[],
                errors=[f"{e.path}: {e.message}" for e in validation.errors],
                # warnings are plain strings; formatting them as objects
                # turned every warned-about invalid config into a 500.
                warnings=list(validation.warnings),
            )

        # Apply configuration
        apply_service = ConfigApplyService(db, config.metadata.name, owner_user_id=user_id)
        result = await apply_service.apply_config(config, dry_run=apply_request.dryRun)

        # Add validation warnings to result
        result.warnings.extend(validation.warnings)

        return result

    except Exception as e:
        logger.error(f"Error applying config: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error applying config: {str(e)}")


@router.get("/export")
async def export_config(
    request: Request,
    include_secrets: bool = False,
    managed_only: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """
    Export current configuration as YAML

    Query Parameters:
    - include_secrets: Include encrypted secrets (default: false)
    - managed_only: Only export config-managed resources (default: false)
    """
    user_id, permissions = current_user_data

    # Check permission
    perm = "sinas.config.read:all"
    if not check_permission(permissions, perm):
        set_permission_used(request, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to export config")

    set_permission_used(request, perm, has_perm=True)

    try:
        export_service = ConfigExportService(
            db, include_secrets=include_secrets, managed_only=managed_only
        )
        yaml_config = await export_service.export_config()

        from fastapi.responses import Response

        return Response(content=yaml_config, media_type="application/x-yaml")

    except Exception as e:
        logger.error(f"Error exporting config: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error exporting config: {str(e)}")


def _require_config_read(request: Request, permissions: dict) -> None:
    perm = "sinas.config.read:all"
    if not check_permission(permissions, perm):
        set_permission_used(request, perm, has_perm=False)
        raise HTTPException(status_code=403, detail="Not authorized to read config history")
    set_permission_used(request, perm, has_perm=True)


@router.get("/history", response_model=list[ConfigRevisionResponse])
async def list_config_history(
    request: Request,
    kind: Optional[str] = Query(None, description="Resource kind, e.g. 'schedules'"),
    key: Optional[str] = Query(None, description="Resource key, e.g. a schedule name"),
    resource_id: Optional[uuid.UUID] = Query(
        None, description="Follow one resource across renames"
    ),
    before: Optional[int] = Query(
        None, description="Only revisions older than this id (keyset pagination)"
    ),
    limit: int = Query(50, ge=1, le=500),
    include_spec: bool = Query(False, description="Include each revision's full spec"),
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """Change history of configurable resources, newest first.

    Every change made through any channel — console, API, config apply,
    package install — is recorded, in the same transaction as the change.
    """
    _, permissions = current_user_data
    _require_config_read(request, permissions)

    stmt = select(ConfigRevision).order_by(ConfigRevision.id.desc()).limit(limit)
    if kind:
        stmt = stmt.where(ConfigRevision.resource_kind == kind)
    if key:
        stmt = stmt.where(ConfigRevision.resource_key == key)
    if resource_id:
        stmt = stmt.where(ConfigRevision.resource_id == resource_id)
    if before is not None:
        stmt = stmt.where(ConfigRevision.id < before)

    revisions = (await db.execute(stmt)).scalars().all()
    responses = [ConfigRevisionResponse.model_validate(r) for r in revisions]
    if not include_spec:
        for response in responses:
            response.spec = None
    return responses


@router.get("/history/{revision_id}", response_model=ConfigRevisionResponse)
async def get_config_revision(
    request: Request,
    revision_id: int,
    db: AsyncSession = Depends(get_db),
    current_user_data: tuple = Depends(get_current_user_with_permissions),
):
    """One revision, including the full spec it recorded."""
    _, permissions = current_user_data
    _require_config_read(request, permissions)

    revision = await db.get(ConfigRevision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail=f"Revision {revision_id} not found")
    return ConfigRevisionResponse.model_validate(revision)
