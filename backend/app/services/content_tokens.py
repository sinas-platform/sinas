"""Token refresh and base64 stripping utilities for message content."""
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import jwt as jose_jwt

from app.core.config import settings
from app.services.file_storage import generate_file_url

logger = logging.getLogger(__name__)


def generate_component_render_token(
    namespace: str, name: str, user_id: str, expires_in: int = 3600
) -> str:
    """Generate a signed render token for a component."""
    payload = {
        "namespace": namespace,
        "name": name,
        "sub": user_id,
        "purpose": "component_render",
        "exp": int((datetime.now(UTC) + timedelta(seconds=expires_in)).timestamp()),
    }
    return jose_jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def refresh_sinas_image_urls(content_parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Refresh expired SINAS file-serve URLs in multimodal content.

    Detects image parts whose URL points to /files/serve/{jwt}, decodes the
    expired JWT to extract file_id + version, and regenerates a fresh signed
    URL.  External URLs are left untouched.
    """
    refreshed: list[dict[str, Any]] = []
    for part in content_parts:
        if part.get("type") != "image":
            refreshed.append(part)
            continue

        url = part.get("image", "")
        if "/files/serve/" not in url:
            # External URL — pass through
            refreshed.append(part)
            continue

        # Extract the JWT token (last path segment)
        token = url.rsplit("/files/serve/", 1)[-1]
        try:
            payload = jose_jwt.decode(
                token,
                settings.secret_key,
                algorithms=[settings.algorithm],
                options={"verify_exp": False},
            )
            file_id = payload.get("file_id")
            version = payload.get("version")
            if file_id is None or version is None:
                raise ValueError("Missing file_id or version in token")

            # Keep existing URL if token still has > 10 min remaining
            exp = payload.get("exp", 0)
            if exp - time.time() > 600:
                refreshed.append(part)
                continue

            new_url = generate_file_url(str(file_id), version)
            if new_url:
                refreshed.append({**part, "image": new_url})
            else:
                # Domain not set / localhost — drop image, add placeholder
                refreshed.append({"type": "text", "text": "[Image unavailable]"})
        except Exception:
            logger.debug("Failed to refresh SINAS image URL, replacing with placeholder", exc_info=True)
            refreshed.append({"type": "text", "text": "[Image no longer available]"})

    return refreshed


def refresh_component_render_tokens(
    content_parts: list[dict[str, Any]], user_id: str
) -> list[dict[str, Any]]:
    """Refresh expired component render tokens in multimodal content.

    Detects component parts with a render_token, decodes the (possibly expired)
    JWT to extract namespace + name, and regenerates a fresh signed token.
    """
    refreshed: list[dict[str, Any]] = []
    for part in content_parts:
        if part.get("type") != "component" or not part.get("render_token"):
            refreshed.append(part)
            continue

        token = part["render_token"]
        try:
            payload = jose_jwt.decode(
                token,
                settings.secret_key,
                algorithms=[settings.algorithm],
                options={"verify_exp": False},
            )
            namespace = payload.get("namespace")
            name = payload.get("name")
            if not namespace or not name:
                raise ValueError("Missing namespace or name in token")

            # Keep existing token if still > 10 min remaining
            exp = payload.get("exp", 0)
            if exp - time.time() > 600:
                refreshed.append(part)
                continue

            new_token = generate_component_render_token(namespace, name, user_id)
            refreshed.append({**part, "render_token": new_token})
        except Exception:
            logger.debug(
                "Failed to refresh component render token", exc_info=True
            )
            refreshed.append(part)

    return refreshed


def strip_base64_data(content: str | None) -> str | None:
    """Strip inline base64 data from message content to reduce payload size.

    Replaces base64 data URIs with a placeholder while keeping URLs intact.
    Skipped on localhost (no DOMAIN) since LLMs can't fetch local URLs and need
    the inline data.
    """
    domain = settings.domain
    if not domain or domain.lower() in ("localhost", "127.0.0.1"):
        return content

    if not content:
        return content

    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return content

    if not isinstance(parsed, list):
        return content

    stripped = []
    for part in parsed:
        if not isinstance(part, dict):
            stripped.append(part)
            continue

        p = dict(part)
        # Image: strip data URIs (data:image/...) but keep regular URLs
        if p.get("type") == "image" and isinstance(p.get("image"), str):
            if p["image"].startswith("data:"):
                p["image"] = "data:stripped"
        # Audio: always inline base64
        if p.get("type") == "audio" and p.get("data"):
            p["data"] = "stripped"
        # File: strip inline base64 file data
        if p.get("type") == "file" and p.get("file_data"):
            p["file_data"] = "stripped"

        stripped.append(p)

    return json.dumps(stripped)


def refresh_message_tokens(content: str | None, user_id: str) -> str | None:
    """Refresh expired tokens (image URLs + component render tokens) in message content."""
    if not content:
        return content

    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return content

    if isinstance(parsed, list):
        parsed = refresh_sinas_image_urls(parsed)
        parsed = refresh_component_render_tokens(parsed, user_id)
        return json.dumps(parsed)

    if isinstance(parsed, dict) and parsed.get("type") == "component" and parsed.get("render_token"):
        refreshed = refresh_component_render_tokens([parsed], user_id)
        return json.dumps(refreshed[0])

    return content
