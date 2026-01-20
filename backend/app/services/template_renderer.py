"""Centralized Jinja2 template rendering service.

Used for:
1. Agent system prompt templating (with agent input context)
2. Function parameter templating (with agent input context)

Security: Uses sandboxed Jinja2 environment with autoescape enabled.
"""
from jinja2 import Environment, StrictUndefined, select_autoescape
from typing import Dict, Any, Optional


# Sandboxed Jinja2 environment for security
_jinja_env = Environment(
    undefined=StrictUndefined,  # Fail on undefined variables
    autoescape=select_autoescape(default_for_string=True, default=True),  # XSS protection
    trim_blocks=True,
    lstrip_blocks=True
)


def render_template(template_str: str, context: Dict[str, Any]) -> str:
    """
    Render a Jinja2 template with given context.

    Args:
        template_str: Jinja2 template string (e.g., "Hello {{name}}")
        context: Variables for template rendering (e.g., {"name": "World"})

    Returns:
        Rendered string

    Raises:
        jinja2.exceptions.TemplateError: If template is invalid or missing variables
    """
    template = _jinja_env.from_string(template_str)
    return template.render(**context)


def render_function_parameters(
    function_params: Dict[str, Any],
    context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Render function parameters, parsing Jinja2 templates in values.

    Args:
        function_params: Function parameters with potential Jinja2 templates
                        Example: {"city": "{{my_city}}", "units": "metric"}
        context: Variables for template rendering
                Example: {"my_city": "London"}

    Returns:
        Rendered parameters with templates resolved
        Example: {"city": "London", "units": "metric"}

    Raises:
        jinja2.exceptions.TemplateError: If template is invalid or missing variables
    """
    rendered = {}
    for key, value in function_params.items():
        if isinstance(value, str):
            # Render string values as Jinja2 templates
            rendered[key] = render_template(value, context)
        elif isinstance(value, dict):
            # Recursively render nested dicts
            rendered[key] = render_function_parameters(value, context)
        elif isinstance(value, list):
            # Render list items
            rendered[key] = [
                render_template(item, context) if isinstance(item, str) else item
                for item in value
            ]
        else:
            # Pass through non-string values (int, float, bool, None)
            rendered[key] = value

    return rendered


def validate_template(template_str: str) -> Optional[str]:
    """
    Validate a Jinja2 template syntax.

    Args:
        template_str: Template string to validate

    Returns:
        None if valid, error message if invalid
    """
    try:
        _jinja_env.from_string(template_str)
        return None
    except Exception as e:
        return f"Invalid template syntax: {str(e)}"
