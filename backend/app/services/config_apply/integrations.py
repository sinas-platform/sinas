"""
Integration appliers: templates. (Webhooks, schedules and database triggers
moved to per-resource appliers in app/services/resources.)
"""
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.template import Template

from app.services.config_apply.normalizers import should_skip_existing

logger = logging.getLogger(__name__)



async def apply_templates(
    db: AsyncSession,
    templates: list,
    dry_run: bool,
    managed_by: str,
    config_name: str,
    owner_user_id: str,
    calculate_hash: Any,
    track_change: Any,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Apply template configurations"""
    for tmpl_config in templates:
        resource_name = f"{tmpl_config.namespace}/{tmpl_config.name}"
        try:
            stmt = select(Template).where(
                Template.namespace == tmpl_config.namespace,
                Template.name == tmpl_config.name,
            )
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            config_hash = calculate_hash(
                {
                    "namespace": tmpl_config.namespace,
                    "name": tmpl_config.name,
                    "description": tmpl_config.description,
                    "title": tmpl_config.title,
                    "html_content": tmpl_config.htmlContent,
                    "text_content": tmpl_config.textContent,
                    "variable_schema": tmpl_config.variableSchema or {},
                }
            )

            if existing:
                if should_skip_existing(existing, managed_by, config_name, config_hash, "templates", resource_name, track_change, warnings):
                    continue

                if not dry_run:
                    existing.description = tmpl_config.description
                    existing.title = tmpl_config.title
                    existing.html_content = tmpl_config.htmlContent
                    existing.text_content = tmpl_config.textContent
                    existing.variable_schema = tmpl_config.variableSchema or {}
                    existing.is_active = True
                    existing.config_checksum = config_hash
                    existing.updated_at = datetime.utcnow()

                track_change("update", "templates", resource_name)

            else:
                if not dry_run:
                    new_template = Template(
                        namespace=tmpl_config.namespace,
                        name=tmpl_config.name,
                        description=tmpl_config.description,
                        title=tmpl_config.title,
                        html_content=tmpl_config.htmlContent,
                        text_content=tmpl_config.textContent,
                        variable_schema=tmpl_config.variableSchema or {},
                        user_id=owner_user_id,
                        created_by=owner_user_id,
                        updated_by=owner_user_id,
                        is_active=True,
                        managed_by=managed_by,
                        config_name=config_name,
                        config_checksum=config_hash,
                    )
                    db.add(new_template)

                track_change("create", "templates", resource_name)

        except Exception as e:
            errors.append(f"Error applying template '{resource_name}': {str(e)}")


