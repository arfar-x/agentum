"""Encryption at rest for credentials and paused-turn payloads."""

from __future__ import annotations

import hashlib
import secrets

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


def hash_key(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def new_api_key() -> str:
    return "agw_" + secrets.token_urlsafe(32)
