"""Random secrets and their storage hashes. Raw values only ever leave in cookies/emails."""

import hashlib
import secrets


def new_secret(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def same(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode(), b.encode())
