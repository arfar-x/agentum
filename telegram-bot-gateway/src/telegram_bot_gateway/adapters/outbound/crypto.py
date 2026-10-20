"""Encryption at rest for API keys."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class Vault:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode()

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("a stored key cannot be decrypted with the configured CREDS_KEY") from exc
