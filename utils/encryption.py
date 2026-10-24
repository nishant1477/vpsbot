"""Arqon Bot — encryption utilities (Fernet)."""
import base64
import hashlib
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from config import settings


def _build_fernet(key: str) -> Fernet:
    """Accept any key format: raw Fernet key, arbitrary string, or env default."""
    if not key:
        key = "arqon-insecure-default-key"
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        # Derive a proper 32-byte urlsafe base64 key from any input string
        derived = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())
        return Fernet(derived)


fernet = _build_fernet(settings.ENCRYPTION_KEY)


class Encryption:
    """Small wrapper kept for backwards compatibility."""

    @staticmethod
    def encrypt(value: str) -> str:
        if value is None:
            return ""
        return f"enc:{fernet.encrypt(str(value).encode()).decode()}"

    @staticmethod
    def decrypt(value: str) -> str:
        if not value:
            return ""
        if value.startswith("enc:"):
            try:
                return fernet.decrypt(value[4:].encode()).decode()
            except (InvalidToken, Exception):
                return ""
        # legacy plaintext passthrough
        return value

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode()

    @staticmethod
    def generate_ssh_key(comment: str = "arqon-bot") -> Optional[tuple]:
        """Generate an ED25519 SSH keypair without shelling out to ssh-keygen.

        Returns (private_openssl_pem, public_openssh) or None if cryptography
        lacks OpenSSH serialization support.
        """
        try:
            from cryptography.hazmat.primitives.asymmetric import ed25519
            from cryptography.hazmat.primitives import serialization
            key = ed25519.Ed25519PrivateKey.generate()
            private_pem = key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.OpenSSH,
                encryption_algorithm=serialization.NoEncryption(),
            ).decode()
            public_open = key.public_key().public_bytes(
                encoding=serialization.Encoding.OpenSSH,
                format=serialization.PublicFormat.OpenSSH,
            ).decode() + f" {comment}"
            return private_pem, public_open
        except Exception:
            return None


# Module-level convenience instance + functions (legacy names)
encryption = Encryption()


def encrypt_value(value: str) -> str:
    return Encryption.encrypt(value)


def decrypt_value(value: str) -> str:
    return Encryption.decrypt(value)


def generate_api_key(length: int = 32) -> str:
    import secrets
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def mask_secret(value: str, visible: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= visible:
        return "*" * len(value)
    return value[:visible] + "*" * (len(value) - visible)


def get_fernet() -> Fernet:
    return fernet


# Re-export os so legacy `from utils.encryption import os` patterns don't explode
_ = os
