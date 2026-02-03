"""
Universal content schema that works across all LLM providers.

Users send messages in this universal format, and we convert to provider-specific
format internally before sending to OpenAI/Mistral/Ollama.
"""
from typing import Literal, NotRequired, Union

from typing_extensions import TypedDict

# ============================================================================
# UNIVERSAL SCHEMA (User-Facing)
# ============================================================================


class TextContent(TypedDict):
    """Text content - universal across all providers."""

    type: Literal["text"]
    text: str


class ImageContent(TypedDict):
    """
    Image content - universal format.

    Supports:
    - HTTPS URLs: "https://example.com/image.jpg"
    - Data URLs: "data:image/png;base64,iVBORw0..."
    """

    type: Literal["image"]
    image: str  # URL or data URL
    detail: NotRequired[Literal["low", "high", "auto"]]  # OpenAI-specific, ignored by others


class AudioContent(TypedDict):
    """
    Audio content - universal format.

    Base64 encoded audio with format specification.
    """

    type: Literal["audio"]
    data: str  # Base64 encoded audio
    format: Literal["wav", "mp3", "m4a", "ogg"]  # Audio format


class FileContent(TypedDict):
    """
    File/Document content - universal format.

    Can be:
    - Base64 encoded file data
    - HTTPS URL to file (Mistral-style)
    - File ID from upload (OpenAI-style)
    """

    type: Literal["file"]
    # At least one of these must be provided:
    file_data: NotRequired[str]  # Base64 encoded file
    file_url: NotRequired[str]  # HTTPS URL to file
    file_id: NotRequired[str]  # Uploaded file ID (OpenAI)
    filename: NotRequired[str]  # Filename (recommended)
    mime_type: NotRequired[str]  # MIME type (optional)


# Union of all universal content types
UniversalContent = Union[TextContent, ImageContent, AudioContent, FileContent]

# Message content: string or array of universal content
UniversalMessageContent = Union[str, list[UniversalContent]]


# ============================================================================
# PROVIDER-SPECIFIC FORMATS (Internal Use Only)
# ============================================================================

# These are used internally after converting from universal format


class OpenAITextContent(TypedDict):
    type: Literal["text"]
    text: str


class OpenAIImageContent(TypedDict):
    type: Literal["image_url"]
    image_url: Union[str, dict[str, str]]  # Can be string or {url, detail}


class OpenAIAudioContent(TypedDict):
    type: Literal["input_audio"]
    input_audio: dict[str, str]  # {data, format}


class OpenAIFileContent(TypedDict):
    type: Literal["file"]
    file: dict[str, str]  # {file_id} or {file_data, filename}


class MistralTextContent(TypedDict):
    type: Literal["text"]
    text: str


class MistralImageContent(TypedDict):
    type: Literal["image_url"]
    image_url: str


class MistralAudioContent(TypedDict):
    type: Literal["input_audio"]
    input_audio: str  # Just base64, no format


class MistralDocumentContent(TypedDict):
    type: Literal["document_url"]
    document_url: str
    document_name: NotRequired[str]


# Provider-specific content unions
OpenAIContent = Union[OpenAITextContent, OpenAIImageContent, OpenAIAudioContent, OpenAIFileContent]
MistralContent = Union[
    MistralTextContent, MistralImageContent, MistralAudioContent, MistralDocumentContent
]
OllamaContent = Union[OpenAITextContent, OpenAIImageContent]  # Ollama uses OpenAI format
