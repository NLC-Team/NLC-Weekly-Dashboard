"""At-rest encryption for small secrets (the SMTP password).

The database can be copied, backed up, or (per the sharing setup) live on a
network path — so secrets inside it must not be readable from the DB file
alone. Values are encrypted with Fernet (AES-128-CBC + HMAC) under a key that
lives in the per-machine data dir next to `secret_key`, NEVER in the database.
Stealing the DB file without also compromising the host yields ciphertext.

Encrypted values are stored as strings with the `ENC_PREFIX` marker so old
plaintext values are recognised and upgraded transparently on the next save.

If the `cryptography` package is missing, the app still runs: values are
stored as before (plaintext) and `available()` is False so the UI can warn.
"""
from __future__ import annotations

import os
from pathlib import Path

import config

ENC_PREFIX = "enc:v1:"

try:
    from cryptography.fernet import Fernet, InvalidToken
    _HAVE_CRYPTO = True
except ImportError:  # keep the dashboard usable without the dependency
    Fernet = None  # type: ignore[assignment]
    InvalidToken = Exception  # type: ignore[assignment]
    _HAVE_CRYPTO = False


def available() -> bool:
    """True when real encryption is available on this machine."""
    return _HAVE_CRYPTO


def _key_path() -> Path:
    return config.default_data_dir() / "vault_key"


def _get_or_create_key() -> bytes | None:
    if not _HAVE_CRYPTO:
        return None
    p = _key_path()
    try:
        if p.exists():
            existing = p.read_bytes().strip()
            if existing:
                return existing
    except OSError:
        pass
    key = Fernet.generate_key()
    try:
        p.write_bytes(key)
        try:
            os.chmod(p, 0o600)  # best-effort; ignored on some filesystems
        except OSError:
            pass
    except OSError:
        return None  # can't persist a key -> don't encrypt (value would be lost)
    return key


def encrypt(value: str) -> str:
    """Encrypt a secret for storage. Falls back to the plain value when
    encryption isn't possible (missing package / unwritable key file)."""
    if not value or value.startswith(ENC_PREFIX):
        return value
    key = _get_or_create_key()
    if not key:
        return value
    return ENC_PREFIX + Fernet(key).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(stored: str) -> str:
    """Reverse of encrypt(). Plaintext (legacy) values pass through unchanged.
    An undecryptable token (key file lost / DB moved to another machine)
    returns "" — the admin just re-enters the password in Settings."""
    if not stored or not stored.startswith(ENC_PREFIX):
        return stored or ""
    key = _get_or_create_key()
    if not key:
        return ""
    try:
        return Fernet(key).decrypt(stored[len(ENC_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""
