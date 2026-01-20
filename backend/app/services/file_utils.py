"""Utilities for handling file uploads and conversions for multimodal messages."""
import base64
import mimetypes
from typing import Optional
from pathlib import Path


def file_to_base64_data_url(file_content: bytes, filename: str) -> str:
    """
    Convert file bytes to a base64 data URL.

    Args:
        file_content: Raw file bytes
        filename: Original filename (used to detect MIME type)

    Returns:
        Base64 data URL string like "data:image/png;base64,iVBORw0KG..."
    """
    # Detect MIME type from filename
    mime_type, _ = mimetypes.guess_type(filename)

    # Fallback MIME types if detection fails
    if not mime_type:
        ext = Path(filename).suffix.lower()
        fallback_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
            '.mp3': 'audio/mpeg',
            '.wav': 'audio/wav',
            '.m4a': 'audio/m4a',
            '.ogg': 'audio/ogg',
            '.pdf': 'application/pdf',
            '.txt': 'text/plain',
            '.json': 'application/json',
        }
        mime_type = fallback_types.get(ext, 'application/octet-stream')

    # Encode to base64
    b64_data = base64.b64encode(file_content).decode('utf-8')

    # Return data URL
    return f"data:{mime_type};base64,{b64_data}"


def base64_to_file_content(data_url: str) -> tuple[bytes, Optional[str]]:
    """
    Convert a base64 data URL back to file bytes.

    Args:
        data_url: Base64 data URL string like "data:image/png;base64,iVBORw0KG..."

    Returns:
        Tuple of (file_bytes, mime_type)
    """
    if not data_url.startswith('data:'):
        raise ValueError("Invalid data URL format")

    # Parse data URL: data:mime/type;base64,<data>
    parts = data_url[5:].split(';base64,', 1)
    if len(parts) != 2:
        raise ValueError("Invalid data URL format - missing base64 marker")

    mime_type = parts[0]
    b64_data = parts[1]

    # Decode base64
    file_content = base64.b64decode(b64_data)

    return file_content, mime_type


def is_supported_image(filename: str) -> bool:
    """Check if file is a supported image type."""
    ext = Path(filename).suffix.lower()
    return ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']


def is_supported_audio(filename: str) -> bool:
    """Check if file is a supported audio type."""
    ext = Path(filename).suffix.lower()
    return ext in ['.mp3', '.wav', '.m4a', '.ogg', '.flac']


def is_supported_document(filename: str) -> bool:
    """Check if file is a supported document type."""
    ext = Path(filename).suffix.lower()
    return ext in ['.pdf', '.txt', '.md', '.json', '.csv', '.docx', '.xlsx']
