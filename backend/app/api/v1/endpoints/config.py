"""
Declarative configuration endpoints
Handles applying, validating, and exporting SINAS configuration
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_with_permissions, set_permission_used
from app.core.database import get_db
from app.core.permissions import check_permission
from app.schemas.config import (
    ConfigApplyRequest,
    ConfigApplyResponse,
    ConfigValidateRequest,
    ConfigValidateResponse,
)
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
    from app.schemas.config import ValidationError as SchemaValidationError

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
                warnings=[f"{w.path}: {w.message}" for w in validation.warnings],
            )

        # Apply configuration
        apply_service = ConfigApplyService(db, config.metadata.name)
        result = await apply_service.apply_config(config, dry_run=apply_request.dryRun)

        # Add validation warnings to result
        result.warnings.extend([f"{w.path}: {w.message}" for w in validation.warnings])

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
