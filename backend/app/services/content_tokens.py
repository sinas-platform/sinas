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


def _refresh_file_serve_url(url: str) -> str | None:
    """Refresh a /files/serve/ URL if its token is near expiry.

    Returns refreshed URL, or None if refresh fails.
    """
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
            return None

        # Keep existing URL if token still has > 10 min remaining
        exp = payload.get("exp", 0)
        if exp - time.time() > 600:
            return url

        return generate_file_url(str(file_id), version) or None
    except Exception:
        logger.debug("Failed to refresh file-serve URL", exc_info=True)
        return None


def refresh_sinas_file_urls(content_parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Refresh expired SINAS file-serve URLs in multimodal content.

    Handles all content types that can reference /files/serve/ URLs:
    image (image field), audio (data field if URL), file (file_url field).
    External URLs are left untouched.
    """
    refreshed: list[dict[str, Any]] = []
    for part in content_parts:
        ptype = part.get("type")

        if ptype == "image":
            url = part.get("image", "")
            if "/files/serve/" not in url:
                refreshed.append(part)
                continue
            new_url = _refresh_file_serve_url(url)
            if new_url:
                refreshed.append({**part, "image": new_url})
            else:
                refreshed.append({"type": "text", "text": "[Image no longer available]"})

        elif ptype == "audio":
            url = part.get("url", "")
            if "/files/serve/" in url:
                new_url = _refresh_file_serve_url(url)
                if new_url:
                    refreshed.append({**part, "url": new_url})
                else:
                    refreshed.append({"type": "text", "text": "[Audio no longer available]"})
            else:
                refreshed.append(part)

        elif ptype == "file":
            url = part.get("file_url", "")
            if "/files/serve/" in url:
                new_url = _refresh_file_serve_url(url)
                if new_url:
                    refreshed.append({**part, "file_url": new_url})
                else:
                    refreshed.append({"type": "text", "text": f"[File '{part.get('filename', '')}' no longer available]"})
            else:
                refreshed.append(part)

        else:
            refreshed.append(part)

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
        ptype = p.get("type")

        if ptype == "image" and isinstance(p.get("image"), str):
            if p["image"].startswith("data:"):
                # Inline data: URI — replace with text placeholder
                stripped.append({"type": "text", "text": "[User sent an image (inline data, no longer available)]"})
                continue

        elif ptype == "audio" and p.get("data") and not p.get("url"):
            # Inline base64 audio without a persistent URL — replace with text
            fmt = p.get("format", "audio")
            stripped.append({"type": "text", "text": f"[User sent {fmt} audio (inline data, no longer available — upload to a collection for persistence)]"})
            continue

        elif ptype == "file" and p.get("file_data") and not p.get("file_url"):
            # Inline base64 file without a persistent URL — replace with text
            fname = p.get("filename", "a file")
            stripped.append({"type": "text", "text": f"[User sent '{fname}' (inline data, no longer available — upload to a collection for persistence)]"})
            continue

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
        parsed = refresh_sinas_file_urls(parsed)
        parsed = refresh_component_render_tokens(parsed, user_id)
        return json.dumps(parsed)

    if isinstance(parsed, dict) and parsed.get("type") == "component" and parsed.get("render_token"):
        refreshed = refresh_component_render_tokens([parsed], user_id)
        return json.dumps(refreshed[0])

    return content
