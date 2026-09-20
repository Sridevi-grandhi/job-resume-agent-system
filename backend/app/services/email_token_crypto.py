"""Symmetric encryption for stored Gmail OAuth tokens (Fernet — AES-128-CBC
+ HMAC, authenticated). Tokens are the only thing this module touches;
ordinary Email Summary metadata (message ids, subjects, statuses) is stored
in plaintext exactly like the rest of the app's data.

Driven by EMAIL_TOKEN_ENCRYPTION_KEY, a base64-encoded 32-byte Fernet key
(generate with `Fernet.generate_key()`). Deliberately does NOT fall back to
an ephemeral/derived key when unset — that would either silently store
tokens in plaintext or produce a key that can't decrypt anything after a
restart. Callers must check is_configured() and refuse to store/read
tokens (never crash the backend) when it is False.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


class TokenCryptoNotConfigured(RuntimeError):
    pass


def _key() -> str:
    return (os.getenv("EMAIL_TOKEN_ENCRYPTION_KEY") or "").strip()


def is_configured() -> bool:
    return bool(_key())


def _fernet() -> Fernet:
    key = _key()
    if not key:
        raise TokenCryptoNotConfigured("EMAIL_TOKEN_ENCRYPTION_KEY is not configured.")
    return Fernet(key.encode("utf-8"))


def encrypt_token(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_token(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as error:
        raise TokenCryptoNotConfigured("Stored token could not be decrypted with the current key.") from error
