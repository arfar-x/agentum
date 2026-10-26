"""Encryption at rest for credentials and paused-turn payloads."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class Vault:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode()

    def encrypt(self, plaintext: bytes) -> bytes:
        return self._fernet.encrypt(plaintext)

    def decrypt(self, ciphertext: bytes) -> bytes:
        try:
            return self._fernet.decrypt(bytes(ciphertext))
        except InvalidToken as exc:  # noqa: TRY003
            raise ValueError("stored value cannot be decrypted with the configured key") from exc

