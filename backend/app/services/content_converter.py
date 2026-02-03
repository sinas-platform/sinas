"""
Convert universal content format to provider-specific formats.

This allows users to send the same message format regardless of which
LLM provider (OpenAI, Mistral, Ollama) is being used.
"""
import logging
from typing import Any, Union

logger = logging.getLogger(__name__)


class ContentConverter:
    """Converts universal content format to provider-specific formats."""

    @staticmethod
    def to_openai(content: Union[str, list[dict[str, Any]]]) -> Union[str, list[dict[str, Any]]]:
        """
        Convert universal content format to OpenAI format.

        Conversions:
        - text: passthrough
        - image: {type: "image_url", image_url: {url: ..., detail: ...}}
        - audio: {type: "input_audio", input_audio: {data: ..., format: ...}}
        - file: {type: "file", file: {file_id: ...}} or {file_data: ..., filename: ...}
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

            elif chunk_type == "audio":
                result.append(
                    {
                        "type": "input_audio",
                        "input_audio": {"data": chunk["data"], "format": chunk["format"]},
                    }
                )

            elif chunk_type == "file":
                file_obj = {}

                # Prefer file_id, then file_data
                if "file_id" in chunk:
                    file_obj["file_id"] = chunk["file_id"]
                elif "file_data" in chunk:
                    file_obj["file_data"] = chunk["file_data"]
                    if "filename" in chunk:
                        file_obj["filename"] = chunk["filename"]
                elif "file_url" in chunk:
                    # OpenAI doesn't support URLs for files, log warning
                    logger.warning(
                        "OpenAI doesn't support file URLs. "
                        "Use file_data (base64) or upload file and use file_id instead."
                    )
                    continue

                result.append({"type": "file", "file": file_obj})

            else:
                # Unknown type, pass through as-is
                result.append(chunk)

        return result

    @staticmethod
    def to_mistral(content: Union[str, list[dict[str, Any]]]) -> Union[str, list[dict[str, Any]]]:
        """
        Convert universal content format to Mistral format.

        Conversions:
        - text: passthrough
        - image: {type: "image_url", image_url: "..."} (string, not object)
        - audio: {type: "input_audio", input_audio: "..."} (just base64, no format)
        - file: {type: "document_url", document_url: "...", document_name: "..."}
        """
        if isinstance(content, str):
            return content

        result = []
        for chunk in content:
            chunk_type = chunk.get("type")

            if chunk_type == "text":
                result.append({"type": "text", "text": chunk["text"]})

            elif chunk_type == "image":
                # Mistral wants just the URL string, not an object
                result.append({"type": "image_url", "image_url": chunk["image"]})

            elif chunk_type == "audio":
                # Mistral wants just the base64 string, no format field
                result.append({"type": "input_audio", "input_audio": chunk["data"]})

            elif chunk_type == "file":
                # Mistral prefers URLs for documents
                if "file_url" in chunk:
                    result.append(
                        {
                            "type": "document_url",
                            "document_url": chunk["file_url"],
                            "document_name": chunk.get("filename"),
                        }
                    )
                elif "file_data" in chunk:
                    # Mistral doesn't support inline file data well
                    logger.warning(
                        "Mistral prefers file URLs. "
                        "Consider uploading file and providing URL via file_url."
                    )
                    # We could try to use image_url with data URL for some file types
                    # but for now, skip it
                    continue
                elif "file_id" in chunk:
                    logger.warning("Mistral doesn't support OpenAI file IDs.")
                    continue

            else:
                # Unknown type, pass through as-is
                result.append(chunk)

        return result

    @staticmethod
    def to_ollama(content: Union[str, list[dict[str, Any]]]) -> Union[str, list[dict[str, Any]]]:
        """
        Convert universal content format to Ollama format.

        Ollama uses OpenAI-compatible format but with limited support:
        - text: supported
        - image: supported (for vision models like llava)
        - audio: NOT supported
        - file: NOT supported
        """
        if isinstance(content, str):
            return content

        result = []
        for chunk in content:
            chunk_type = chunk.get("type")

            if chunk_type == "text":
                result.append({"type": "text", "text": chunk["text"]})

            elif chunk_type == "image":
                # Ollama uses OpenAI format for images
                image_url = chunk["image"]
                result.append(
                    {"type": "image_url", "image_url": image_url}  # Can be URL or data URL
                )

            elif chunk_type == "audio":
                logger.warning("Ollama doesn't support audio input. Skipping audio chunk.")
                continue

            elif chunk_type == "file":
                logger.warning("Ollama doesn't support file input. Skipping file chunk.")
                continue

            else:
                # Unknown type, pass through as-is
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
            provider_type: "openai", "mistral", or "ollama"

        Returns:
            Provider-specific content format
        """
        provider_type = provider_type.lower()

        if provider_type == "openai":
            return ContentConverter.to_openai(content)
        elif provider_type == "mistral":
            return ContentConverter.to_mistral(content)
        elif provider_type == "ollama":
            return ContentConverter.to_ollama(content)
        else:
            # Unknown provider, pass through as-is
            logger.warning(
                f"Unknown provider type: {provider_type}. Passing content through as-is."
            )
            return content
