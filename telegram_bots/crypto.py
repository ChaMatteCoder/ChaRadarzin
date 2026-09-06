from __future__ import annotations

import hashlib
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class SecretDecryptionError(ValueError):
    """Raised without exposing the encrypted or plaintext bot token."""


@dataclass(frozen=True, slots=True)
class EncryptedSecret:
    ciphertext: str
    key_version: int


class BotTokenCipher:
    def __init__(self, keyring: dict[int, Fernet]) -> None:
        if not keyring:
            raise ImproperlyConfigured("Nenhuma chave Fernet foi configurada.")
        self._keyring = keyring
        self.current_version = max(keyring)

    @classmethod
    def from_settings(cls) -> "BotTokenCipher":
        raw_keyring = settings.BOT_TOKEN_ENCRYPTION_KEYS
        keyring: dict[int, Fernet] = {}
        for raw_entry in raw_keyring.split(","):
            entry = raw_entry.strip()
            if not entry:
                continue
            try:
                raw_version, raw_key = entry.split(":", 1)
                version = int(raw_version)
                if version <= 0 or version in keyring:
                    raise ValueError
                keyring[version] = Fernet(raw_key.encode("ascii"))
            except (ValueError, UnicodeEncodeError) as exc:
                raise ImproperlyConfigured(
                    "BOT_TOKEN_ENCRYPTION_KEYS deve usar versao:chave_fernet."
                ) from exc
        return cls(keyring)

    def encrypt(self, plaintext: str) -> EncryptedSecret:
        if not plaintext or any(character.isspace() for character in plaintext):
            raise ValueError("Token do bot ausente ou invalido.")
        ciphertext = self._keyring[self.current_version].encrypt(
            plaintext.encode("utf-8")
        )
        return EncryptedSecret(ciphertext.decode("ascii"), self.current_version)

    def decrypt(self, ciphertext: str, key_version: int | None) -> str:
        cipher = self._keyring.get(key_version or -1)
        if cipher is None:
            raise SecretDecryptionError("Versao da chave do token indisponivel.")
        try:
            return cipher.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError) as exc:
            raise SecretDecryptionError("Token do bot nao pode ser descriptografado.") from exc


def webhook_secret_digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("ascii")).hexdigest()
