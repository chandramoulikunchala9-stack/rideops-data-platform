"""Password hashing (Argon2id, §18.2) and the password policy (FR-01)."""

from functools import lru_cache
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 12
MAX_LENGTH = 128  # bounds hashing cost for hostile input

_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=1)


def configure(*, time_cost: int, memory_cost: int) -> None:
    """Tests lower the cost; production keeps the §18.2 parameters."""
    global _hasher, _DUMMY_HASH
    _hasher = PasswordHasher(time_cost=time_cost, memory_cost=memory_cost, parallelism=1)
    _DUMMY_HASH = None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


_DUMMY_HASH: str | None = None


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-ish time: an unknown user still pays for one hash verification."""
    global _DUMMY_HASH
    if password_hash is None:
        _DUMMY_HASH = _DUMMY_HASH or _hasher.hash("dummy-password-for-timing")
        password_hash = _DUMMY_HASH
        password = password + "\0"  # never matches
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@lru_cache
def _common_passwords() -> frozenset[str]:
    lines = (Path(__file__).parent / "common_passwords.txt").read_text(encoding="utf-8").splitlines()
    return frozenset(line.strip().lower() for line in lines if line.strip() and not line.startswith("#"))


def policy_problems(password: str, *, email: str | None = None) -> list[str]:
    problems = []
    if len(password) < MIN_LENGTH:
        problems.append(f"Use at least {MIN_LENGTH} characters.")
    if len(password) > MAX_LENGTH:
        problems.append(f"Use at most {MAX_LENGTH} characters.")
    if password.lower() in _common_passwords():
        problems.append("This password appears in breached-password lists. Choose another.")
    if email and password.lower() == email.lower():
        problems.append("Don't use your email address as the password.")
    return problems
