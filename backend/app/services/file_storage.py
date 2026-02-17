"""File storage abstraction layer."""
import base64
import hashlib
import os
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Optional

from jose import jwt


class FileStorage(ABC):
    """Abstract base class for file storage backends."""

    @abstractmethod
    async def save(self, path: str, data: bytes) -> str:
        """
        Save file data to storage.

        Args:
            path: Relative path where file should be stored
            data: File content as bytes

        Returns:
            Storage path where file was saved
        """
        pass

    @abstractmethod
    async def read(self, path: str) -> bytes:
        """
        Read file data from storage.

        Args:
            path: Relative path to file

        Returns:
            File content as bytes
        """
        pass

    @abstractmethod
    async def delete(self, path: str) -> None:
        """
        Delete file from storage.

        Args:
            path: Relative path to file
        """
        pass

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """
        Check if file exists in storage.

        Args:
            path: Relative path to file

        Returns:
            True if file exists, False otherwise
        """
        pass

    @abstractmethod
    async def get_size(self, path: str) -> int:
        """
        Get file size in bytes.

        Args:
            path: Relative path to file

        Returns:
            File size in bytes
        """
        pass

    @staticmethod
    def calculate_hash(data: bytes) -> str:
        """
        Calculate SHA256 hash of file data.

        Args:
            data: File content as bytes

        Returns:
            SHA256 hash as hex string
        """
        return hashlib.sha256(data).hexdigest()


class LocalFileStorage(FileStorage):
    """Local filesystem storage backend."""

    def __init__(self, base_path: str = "/var/sinas/files"):
        """
        Initialize local file storage.

        Args:
            base_path: Base directory for file storage
        """
        self.base_path = Path(base_path)
        # Ensure base directory exists
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_full_path(self, relative_path: str) -> Path:
        """
        Get full filesystem path from relative path.

        Args:
            relative_path: Relative path from base

        Returns:
            Full Path object

        Raises:
            ValueError: If path tries to escape base directory
        """
        full_path = (self.base_path / relative_path).resolve()
        # Security: Ensure path doesn't escape base directory
        if not str(full_path).startswith(str(self.base_path.resolve())):
            raise ValueError(f"Path '{relative_path}' escapes base directory")
        return full_path

    async def save(self, path: str, data: bytes) -> str:
        """Save file data to local filesystem."""
        full_path = self._get_full_path(path)

        # Create parent directories if they don't exist
        full_path.parent.mkdir(parents=True, exist_ok=True)

        # Write file atomically (write to temp, then rename)
        temp_path = full_path.with_suffix(full_path.suffix + ".tmp")
        try:
            temp_path.write_bytes(data)
            temp_path.rename(full_path)
        except Exception:
            # Clean up temp file on error
            if temp_path.exists():
                temp_path.unlink()
            raise

        return path

    async def read(self, path: str) -> bytes:
        """Read file data from local filesystem."""
        full_path = self._get_full_path(path)

        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        return full_path.read_bytes()

    async def delete(self, path: str) -> None:
        """Delete file from local filesystem."""
        full_path = self._get_full_path(path)

        if full_path.exists():
            full_path.unlink()

    async def exists(self, path: str) -> bool:
        """Check if file exists in local filesystem."""
        full_path = self._get_full_path(path)
        return full_path.exists()

    async def get_size(self, path: str) -> int:
        """Get file size from local filesystem."""
        full_path = self._get_full_path(path)

        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        return full_path.stat().st_size


# Global storage instance
_storage: Optional[FileStorage] = None


def get_storage() -> FileStorage:
    """
    Get the global file storage instance.

    Returns:
        FileStorage instance
    """
    global _storage
    if _storage is None:
        # Initialize with default local storage
        # TODO: Make configurable via environment variables
        storage_backend = os.getenv("FILE_STORAGE_BACKEND", "local")

        if storage_backend == "local":
            base_path = os.getenv("FILE_STORAGE_PATH", "/var/sinas/files")
            _storage = LocalFileStorage(base_path=base_path)
        else:
            raise ValueError(f"Unknown storage backend: {storage_backend}")

    return _storage


def generate_file_url(file_id: str, version: int, expires_in: int = 3600) -> Optional[str]:
    """
    Generate a temporary signed URL for serving a file.

    Returns None if DOMAIN is localhost or not set (caller should fall back to data URL).
    """
    from app.core.config import settings

    domain = settings.domain
    if not domain or domain.lower() in ("localhost", "127.0.0.1"):
        return None

    expire = datetime.now(UTC) + timedelta(seconds=expires_in)
    payload = {
        "file_id": str(file_id),
        "version": version,
        "purpose": "file_serve",
        "exp": int(expire.timestamp()),
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)
    return f"https://{domain}/api/runtime/files/serve/{token}"


async def generate_file_data_url(storage_path: str, content_type: str) -> str:
    """
    Read a file from storage and return a base64 data URL.

    Used as fallback when DOMAIN is localhost (LLM providers can't reach the server).
    """
    storage = get_storage()
    data = await storage.read(storage_path)
    b64 = base64.b64encode(data).decode("utf-8")
    return f"data:{content_type};base64,{b64}"
