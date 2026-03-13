"""
Convert universal content format to provider-specific formats.

This allows users to send the same message format regardless of which
LLM provider (OpenAI, Mistral, Ollama) is being used.

File handling: file uploads are processed by the content filter before
reaching the converter — files are stored in Collections/Files and a
public URL is generated. The converter receives file_url and passes it
to the LLM in the most appropriate format for each provider.

Fallback: if file_data arrives without file_url (content filter not
configured for this file type), text-based files are decoded inline.
"""
import base64
import logging
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)


def _upload_ref_text(chunk: dict[str, Any], kind: str = "file") -> dict[str, Any]:
    """Create a text part referencing an uploaded file/image by its public URL.

    Always emitted alongside any native visual/document part so the LLM
    knows the URL and can pass it to tools (e.g. send_email, execute_code).
    """
    filename = chunk.get("filename", "")
    url = chunk.get("file_url") or chunk.get("image", "")
    label = f"'{filename}' " if filename else ""
    # Never send data: URIs as text — they bloat the context massively
    if url.startswith("data:"):
        return {"type": "text", "text": f"[User uploaded {kind} {label}— file available but no public URL]"}
    return {"type": "text", "text": f"[User uploaded {kind} {label}— public URL: {url}]"}


def _try_inline_text_file(chunk: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Fallback: decode base64 file_data as text when no file_url is available.

    Returns a text part with the file content, or None if decoding fails.
    Used when the content filter hasn't processed the file upload.
    """
    file_data = chunk.get("file_data")
    if not file_data:
        return None

    try:
        decoded = base64.b64decode(file_data).decode("utf-8")
    except (base64.binascii.Error, UnicodeDecodeError):
        return None

    filename = chunk.get("filename", "uploaded file")
    return {"type": "text", "text": f"[Contents of '{filename}']\n{decoded}"}


class ContentConverter:
    """Converts universal content format to provider-specific formats."""

    @staticmethod
    def to_openai(content: Union[str, list[dict[str, Any]]]) -> Union[str, list[dict[str, Any]]]:
        """
        Convert universal content format to OpenAI format.

        Conversions:
        - text: passthrough
        - image: native image_url + text reference with URL for tool use
        - audio: {type: "input_audio", input_audio: {data: ..., format: ...}}
        - file: file_id → native file part; file_url → text reference
        """
        if isinstance(content, str):
            return content

        result = []
        for chunk in content:
            chunk_type = chunk.get("type")

            if chunk_type == "text":
                result.append({"type": "text", "text": chunk["text"]})

            elif chunk_type == "image":
                image_url = chunk["image"]
                detail = chunk.get("detail", "auto")
                result.append(
                    {"type": "image_url", "image_url": {"url": image_url, "detail": detail}}
                )
                # Also emit text reference so LLM can pass URL to tools
                if not image_url.startswith("data:"):
                    result.append(_upload_ref_text(chunk, kind="image"))

            elif chunk_type == "audio":
                result.append(
                    {
                        "type": "input_audio",
                        "input_audio": {"data": chunk["data"], "format": chunk["format"]},
                    }
                )

            elif chunk_type == "file":
                if "file_url" in chunk:
                    result.append(_upload_ref_text(chunk))
                elif "file_id" in chunk:
                    result.append({"type": "file", "file": {"file_id": chunk["file_id"]}})
                else:
                    logger.warning("File part has no file_id or file_url. Skipping.")
                    continue

            else:
                result.append(chunk)

        return result

    @staticmethod
    def to_mistral(content: Union[str, list[dict[str, Any]]]) -> Union[str, list[dict[str, Any]]]:
        """
        Convert universal content format to Mistral format.

        Conversions:
        - text: passthrough
        - image: native image_url + text reference with URL for tool use
        - audio: {type: "input_audio", input_audio: "..."} (just base64, no format)
        - file: file_url → native document_url + text reference
        """
        if isinstance(content, str):
            return content

        result = []
        for chunk in content:
            chunk_type = chunk.get("type")

            if chunk_type == "text":
                result.append({"type": "text", "text": chunk["text"]})

            elif chunk_type == "image":
                image_url = chunk["image"]
                result.append({"type": "image_url", "image_url": image_url})
                if not image_url.startswith("data:"):
                    result.append(_upload_ref_text(chunk, kind="image"))

            elif chunk_type == "audio":
                result.append({"type": "input_audio", "input_audio": chunk["data"]})

            elif chunk_type == "file":
                if "file_url" in chunk:
                    file_url = chunk["file_url"]
                    # Mistral fetches document_url server-side — only works
                    # with publicly reachable URLs (not host.docker.internal)
                    if not file_url.startswith("http://host.docker.internal"):
                        result.append(
                            {
                                "type": "document_url",
                                "document_url": file_url,
                                "document_name": chunk.get("filename"),
                            }
                        )
                    result.append(_upload_ref_text(chunk))
                else:
                    logger.warning("Mistral requires file_url for documents. Skipping file part.")
                    continue

            else:
                result.append(chunk)

        return result

    @staticmethod
    def to_ollama(content: Union[str, list[dict[str, Any]]]) -> Union[str, list[dict[str, Any]]]:
        """
        Convert universal content format to Ollama format.

        Ollama uses OpenAI-compatible format but with limited support:
        - text: supported
        - image: native image_url + text reference with URL for tool use
        - audio: NOT supported
        - file: file_url → text reference
        """
        if isinstance(content, str):
            return content

        result = []
        for chunk in content:
            chunk_type = chunk.get("type")

            if chunk_type == "text":
                result.append({"type": "text", "text": chunk["text"]})

            elif chunk_type == "image":
                image_url = chunk["image"]
                result.append(
                    {"type": "image_url", "image_url": image_url}
                )
                if not image_url.startswith("data:"):
                    result.append(_upload_ref_text(chunk, kind="image"))

            elif chunk_type == "audio":
                logger.warning("Ollama doesn't support audio input. Skipping audio chunk.")
                continue

            elif chunk_type == "file":
                if "file_url" in chunk:
                    result.append(_upload_ref_text(chunk))
                else:
                    logger.warning("Ollama doesn't support file input without URL. Skipping.")
                    continue

            else:
                result.append(chunk)

        return result

    @staticmethod
    def to_anthropic(content: Union[str, list[dict[str, Any]]]) -> Union[str, list[dict[str, Any]]]:
        """
        Convert universal content format to Anthropic format.

        Conversions:
        - text: passthrough
        - image: native image + text reference with URL for tool use
        - audio: NOT supported (skipped with warning)
        - file: PDF with file_data → native document + text reference;
               otherwise file_url → text reference
        """
        if isinstance(content, str):
            return content

        result = []
        for chunk in content:
            chunk_type = chunk.get("type")

            if chunk_type == "text":
                result.append({"type": "text", "text": chunk["text"]})

            elif chunk_type == "image":
                image_value = chunk["image"]
                if image_value.startswith("data:"):
                    header, data = image_value.split(",", 1)
                    media_type = header.split(":")[1].split(";")[0]
                    result.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": data,
                        },
                    })
                else:
                    result.append({
                        "type": "image",
                        "source": {
                            "type": "url",
                            "url": image_value,
                        },
                    })
                    result.append(_upload_ref_text(chunk, kind="image"))

            elif chunk_type == "audio":
                logger.warning("Anthropic doesn't support audio input. Skipping audio chunk.")
                continue

            elif chunk_type == "file":
                if "file_data" in chunk:
                    mime = chunk.get("mime_type", "application/pdf")
                    if mime == "application/pdf":
                        result.append({
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": mime,
                                "data": chunk["file_data"],
                            },
                        })
                    # Also emit text reference if URL available
                    if "file_url" in chunk:
                        result.append(_upload_ref_text(chunk))
                    elif mime != "application/pdf":
                        logger.warning(
                            f"Anthropic only supports PDF natively, got {mime}, and no file_url. Skipping."
                        )
                        continue
                elif "file_url" in chunk:
                    result.append(_upload_ref_text(chunk))
                else:
                    logger.warning("Anthropic requires file_data or file_url. Skipping file chunk.")
                    continue

            else:
                result.append(chunk)

        return result

    @staticmethod
    def convert_message_content(
        content: Union[str, list[dict[str, Any]]], provider_type: str
    ) -> Union[str, list[dict[str, Any]]]:
        """
        Convert message content to provider-specific format.

        Args:
            content: Universal content format
            provider_type: "openai", "mistral", "ollama", or "anthropic"

        Returns:
            Provider-specific content format
        """
        provider_type = provider_type.lower()

        if provider_type == "openai":
            return ContentConverter.to_openai(content)
        elif provider_type == "anthropic":
            return ContentConverter.to_anthropic(content)
        elif provider_type == "mistral":
            return ContentConverter.to_mistral(content)
        elif provider_type == "ollama":
            return ContentConverter.to_ollama(content)
        else:
            logger.warning(
                f"Unknown provider type: {provider_type}. Passing content through as-is."
            )
            return content
