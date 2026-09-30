"""Centralized Jinja2 template rendering service.

Used for:
1. Agent system prompt templating (with agent input context)
2. Function parameter templating (with agent input context)

Security: Uses jinja2 SandboxedEnvironment (blocks attribute traversal such as
__class__/__globals__, which would otherwise be RCE in the API process, since
templates are user-authored). Autoescape is on for the general environment.
"""
import logging
from typing import Any, Optional

from jinja2 import ChainableUndefined, StrictUndefined, select_autoescape
from jinja2.exceptions import SecurityError
from jinja2.sandbox import SandboxedEnvironment

logger = logging.getLogger(__name__)

# SandboxedEnvironment, not Environment: these templates are authored by users
# (agent prompts, function params, webhook messages) but rendered IN-PROCESS in
# the API worker, which holds SECRET_KEY, ENCRYPTION_KEY and DB credentials. A
# plain Environment allows attribute traversal (__class__/__globals__) — i.e.
# code execution — from any template author. autoescape does not prevent this.
_jinja_env = SandboxedEnvironment(
    undefined=StrictUndefined,  # Fail on undefined variables
    autoescape=select_autoescape(default_for_string=True, default=True),  # XSS protection
    trim_blocks=True,
    lstrip_blocks=True,
)


class _LoggingUndefined(ChainableUndefined):
    """Undefined that renders as empty string but logs, so a missing payload
    field never crashes a webhook.

    Exception: a sandbox violation must still fail loudly. SandboxedEnvironment
    signals blocked attribute access by returning an Undefined carrying
    SecurityError; swallowing that here would make an attempted escape look
    identical to a provider renaming a field.
    """

    def __str__(self) -> str:
        if self._undefined_exception is SecurityError:
            self._fail_with_undefined_error()
        logger.warning(
            "Webhook template referenced undefined variable: %s", self._undefined_name
        )
        return ""


# Environment for webhook message/session-key templates: payloads are untrusted
# and providers change their schemas, so undefined variables render empty
# instead of failing. No autoescape — output is plain text, not HTML.
_webhook_jinja_env = SandboxedEnvironment(
    undefined=_LoggingUndefined,
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_webhook_template(template_str: str, context: dict[str, Any]) -> str:
    """Render a webhook message/session-key template against a request payload.

    Undefined variables render as empty strings (and are logged) so that
    unexpected payload shapes never fail the webhook.
    """
    rendered, _ = render_webhook_template_checked(template_str, context)
    return rendered


def render_webhook_template_checked(
    template_str: str, context: dict[str, Any]
) -> tuple[str, bool]:
    """Render a webhook template, reporting whether any variable was undefined.

    Returns (rendered, had_undefined). Callers need the flag because a *partial*
    render is the dangerous case: 'jira-{{ issue.key }}' against a payload with
    no `issue` yields 'jira-', which is non-empty and truthy, so a bare
    emptiness check accepts it — collapsing unrelated events into one session,
    or handing an agent a message with no information in it.
    """
    seen: list[str] = []

    class _Tracking(_LoggingUndefined):
        def __str__(self) -> str:  # noqa: D105 - see _LoggingUndefined
            seen.append(self._undefined_name or "?")
            return super().__str__()

    env = _webhook_jinja_env.overlay(undefined=_Tracking)
    rendered = env.from_string(template_str).render(**context)
    return rendered, bool(seen)


def render_template(template_str: str, context: dict[str, Any]) -> str:
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
    function_params: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
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
                render_template(item, context) if isinstance(item, str) else item for item in value
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
