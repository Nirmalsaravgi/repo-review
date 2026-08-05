"""HMAC webhook verification and Fernet-style token envelope (dev-friendly)."""

from __future__ import annotations

import hashlib
import hmac
from base64 import urlsafe_b64encode

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from repo_core.config import get_settings


def verify_github_signature(body: bytes, signature_header: str | None, secret: str) -> bool:
    if not signature_header or not secret:
        return False
    if not signature_header.startswith("sha256="):
        return False
    expected = signature_header.removeprefix("sha256=")
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, expected)


def _fernet() -> Fernet:
    settings = get_settings()
    # Derive a stable 32-byte urlsafe key from app secret.
    material = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"repo-understanding-v1",
        info=b"installation-token",
    ).derive(settings.app_secret_key.encode("utf-8"))
    key = urlsafe_b64encode(material)
    return Fernet(key)


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Failed to decrypt secret") from exc
