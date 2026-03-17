"""Encryption utilities for sensitive data."""
import logging

from cryptography.fernet import Fernet

from app.core.config import settings

logger = logging.getLogger(__name__)


class EncryptionService:
    """Service for encrypting and decrypting sensitive data."""

    def __init__(self):
        if settings.encryption_key:
            self.key = settings.encryption_key.encode()
        else:
            logger.warning(
                "ENCRYPTION_KEY is not set — generating ephemeral key. "
                "Encrypted data will be LOST on restart. Set ENCRYPTION_KEY in production."
            )
            self.key = Fernet.generate_key()

        self.cipher = Fernet(self.key)

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a string.

        Args:
            plaintext: String to encrypt

        Returns:
            Encrypted string (base64 encoded)
        """
        if not plaintext:
            return plaintext

        encrypted_bytes = self.cipher.encrypt(plaintext.encode())
        return encrypted_bytes.decode()

    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt a string.

        Args:
            ciphertext: Encrypted string (base64 encoded)

        Returns:
            Decrypted plaintext string
        """
        if not ciphertext:
            return ciphertext

        decrypted_bytes = self.cipher.decrypt(ciphertext.encode())
        return decrypted_bytes.decode()


# Global encryption service instance
encryption_service = EncryptionService()
