"""Template rendering service with Jinja2 and schema validation."""
import logging
from typing import Any, Optional

import jsonschema
from jinja2 import BaseLoader, Environment, StrictUndefined, TemplateError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.template import Template

logger = logging.getLogger(__name__)


class TemplateService:
    """Service for rendering templates with Jinja2 and schema validation."""

    def __init__(self):
        # Jinja2 environment with autoescape for variable protection
        # Admin-created template HTML is trusted (not sanitized)
        # Variables are auto-escaped to prevent injection via user input
        self.jinja_env = Environment(
            loader=BaseLoader(),
            autoescape=True,  # Protects variables: {{user_email}} is escaped
            undefined=StrictUndefined,  # Raise errors on undefined variables
        )

    def validate_variables(self, variables: dict[str, Any], schema: dict[str, Any]) -> None:
        """
        Validate variables against JSON schema.

        Args:
            variables: Variables to validate
            schema: JSON schema

        Raises:
            jsonschema.ValidationError if validation fails
        """
        if not schema:
            return  # No schema = no validation

        jsonschema.validate(variables, schema)

    async def render_template(
        self,
        db: AsyncSession,
        template_name: str,
        variables: dict[str, Any],
        namespace: str = "default",
    ) -> tuple[Optional[str], str, Optional[str]]:
        """
        Render a template by namespace/name with given variables.

        Args:
            db: Database session
            template_name: Template name to render
            variables: Variables to pass to template
            namespace: Template namespace (default: "default")

        Returns:
            Tuple of (title, html_content, text_content)

        Raises:
            ValueError: If template not found or inactive
            jsonschema.ValidationError: If variables don't match schema
            TemplateError: If Jinja2 rendering fails
        """
        # Load template from database
        from sqlalchemy import and_

        result = await db.execute(
            select(Template).where(
                and_(
                    Template.namespace == namespace,
                    Template.name == template_name,
                    Template.is_active == True,
                )
            )
        )
        template = result.scalar_one_or_none()

        if not template:
            raise ValueError(f"Template '{namespace}/{template_name}' not found or inactive")

        # Validate variables against schema
        if template.variable_schema:
            try:
                self.validate_variables(variables, template.variable_schema)
            except jsonschema.ValidationError as e:
                logger.error(f"Template variable validation failed for {template_name}: {e}")
                raise

        # Render title (if present)
        rendered_title = None
        if template.title:
            try:
                title_template = self.jinja_env.from_string(template.title)
                rendered_title = title_template.render(**variables)
            except TemplateError as e:
                logger.error(f"Template title rendering failed for {template_name}: {e}")
                raise

        # Render HTML content
        try:
            html_template = self.jinja_env.from_string(template.html_content)
            rendered_html = html_template.render(**variables)
        except TemplateError as e:
            logger.error(f"Template HTML rendering failed for {template_name}: {e}")
            raise

        # Render text content (if present)
        rendered_text = None
        if template.text_content:
            try:
                text_template = self.jinja_env.from_string(template.text_content)
                rendered_text = text_template.render(**variables)
            except TemplateError as e:
                logger.error(f"Template text rendering failed for {template_name}: {e}")
                raise

        return rendered_title, rendered_html, rendered_text

    async def render_inline(
        self,
        html_content: str,
        variables: dict[str, Any],
        title: Optional[str] = None,
        text_content: Optional[str] = None,
        variable_schema: Optional[dict[str, Any]] = None,
    ) -> tuple[Optional[str], str, Optional[str]]:
        """
        Render template from inline content (not from database).

        Useful for programmatic template rendering without storing in DB.

        Args:
            html_content: HTML template string (Jinja2)
            variables: Variables to pass to template
            title: Optional title template string
            text_content: Optional text template string
            variable_schema: Optional JSON schema for variable validation

        Returns:
            Tuple of (title, html_content, text_content)

        Raises:
            jsonschema.ValidationError: If variables don't match schema
            TemplateError: If Jinja2 rendering fails
        """
        # Validate variables against schema
        if variable_schema:
            self.validate_variables(variables, variable_schema)

        # Render title (if present)
        rendered_title = None
        if title:
            title_template = self.jinja_env.from_string(title)
            rendered_title = title_template.render(**variables)

        # Render HTML content
        html_template = self.jinja_env.from_string(html_content)
        rendered_html = html_template.render(**variables)

        # Render text content (if present)
        rendered_text = None
        if text_content:
            text_template = self.jinja_env.from_string(text_content)
            rendered_text = text_template.render(**variables)

        return rendered_title, rendered_html, rendered_text


# Global service instance
template_service = TemplateService()
