"""Envelope encryption for connection secrets (§18.4, FR-16).

    KEK ring  <- LENS_MASTER_KEYS="1:<base64 32 bytes>,2:<base64 32 bytes>"
    per secret: DEK = random 32 bytes
                ciphertext  = AES-256-GCM(DEK, nonce, json(secret), AAD = workspace_id|connection_id)
                wrapped_dek = nonce' || AES-256-GCM(KEK_v, nonce', DEK, AAD = "dek|" + AAD)

The AAD binding means a ciphertext copied onto another connection or workspace fails to decrypt.
Deleting the `connection_secrets` row destroys the only copy of the wrapped DEK (crypto-shred).
"""

import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class KeyRingError(RuntimeError):
    pass


class DecryptError(RuntimeError):
    pass


@dataclass(frozen=True)
class SecretBlob:
    kek_version: int
    wrapped_dek: bytes
    nonce: bytes
    ciphertext: bytes


def aad_for(workspace_id: Any, connection_id: Any) -> bytes:
    return f"{workspace_id}|{connection_id}".encode()


class KeyRing:
    def __init__(self, keys: dict[int, bytes], active_version: int) -> None:
        if not keys:
            raise KeyRingError("LENS_MASTER_KEYS is not configured")
        for version, key in keys.items():
            if len(key) != 32:
                raise KeyRingError(f"KEK version {version} must be 32 bytes")
        if active_version not in keys:
            raise KeyRingError(
                f"LENS_ACTIVE_KEY_VERSION={active_version} is not in LENS_MASTER_KEYS"
            )
        self._keys = dict(keys)
        self.active_version = active_version

    @classmethod
    def from_env_value(cls, value: str | None, active_version: int | None = None) -> "KeyRing":
        if not value or not value.strip():
            raise KeyRingError("LENS_MASTER_KEYS is not configured")
        keys: dict[int, bytes] = {}
        for part in value.split(","):
            version, _, b64 = part.strip().partition(":")
            try:
                keys[int(version)] = base64.b64decode(b64, validate=True)
            except ValueError as exc:
                raise KeyRingError(
                    f"Malformed LENS_MASTER_KEYS entry for version {version!r}"
                ) from exc
        return cls(keys, active_version if active_version is not None else max(keys))

    @staticmethod
    def generate_key() -> str:
        return base64.b64encode(os.urandom(32)).decode()

    @property
    def versions(self) -> list[int]:
        return sorted(self._keys)

    def encrypt(self, plaintext: dict[str, Any], aad: bytes) -> SecretBlob:
        dek = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        ciphertext = AESGCM(dek).encrypt(nonce, json.dumps(plaintext, sort_keys=True).encode(), aad)
        wrap_nonce = os.urandom(12)
        wrapped = wrap_nonce + AESGCM(self._keys[self.active_version]).encrypt(
            wrap_nonce, dek, b"dek|" + aad
        )
        return SecretBlob(self.active_version, wrapped, nonce, ciphertext)

    def _unwrap(self, blob: SecretBlob, aad: bytes) -> bytes:
        kek = self._keys.get(blob.kek_version)
        if kek is None:
            raise DecryptError(f"KEK version {blob.kek_version} is not loaded")
        try:
            return AESGCM(kek).decrypt(blob.wrapped_dek[:12], blob.wrapped_dek[12:], b"dek|" + aad)
        except InvalidTag as exc:
            raise DecryptError(
                "Secret could not be decrypted (wrong key or tampered data)"
            ) from exc

    def decrypt(self, blob: SecretBlob, aad: bytes) -> dict[str, Any]:
        dek = self._unwrap(blob, aad)
        try:
            return json.loads(AESGCM(dek).decrypt(blob.nonce, blob.ciphertext, aad))
        except InvalidTag as exc:
            raise DecryptError(
                "Secret could not be decrypted (wrong key or tampered data)"
            ) from exc

    def rewrap(self, blob: SecretBlob, aad: bytes) -> SecretBlob:
        """KEK rotation: re-wrap the DEK with the active KEK; the data ciphertext is unchanged."""
        dek = self._unwrap(blob, aad)
        wrap_nonce = os.urandom(12)
        wrapped = wrap_nonce + AESGCM(self._keys[self.active_version]).encrypt(
            wrap_nonce, dek, b"dek|" + aad
        )
        return SecretBlob(self.active_version, wrapped, blob.nonce, blob.ciphertext)

    def fingerprint(self, data: dict[str, Any]) -> str:
        """Keyed digest of a config + secret, so a saved test can be matched without storing secrets."""
        key = hmac.new(
            self._keys[self.active_version], b"lens-config-fingerprint", hashlib.sha256
        ).digest()
        return hmac.new(
            key, json.dumps(data, sort_keys=True, default=str).encode(), hashlib.sha256
        ).hexdigest()
