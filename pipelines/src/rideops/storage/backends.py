"""Object storage behind one small interface, so code does not care where files live.

Key layout (identical locally and in S3):
    landing/<entity>/batch_date=YYYY-MM-DD/<entity>_<YYYYMMDD>_<batch>.<csv|jsonl>
Buckets (created by infra/terraform):
    <project>-<env>-raw-<suffix>         landing zone written by source systems
    <project>-<env>-processed-<suffix>   lakehouse bronze/silver (Unity Catalog managed location)
    <project>-<env>-analytics-<suffix>   gold extracts for BI tools

Credentials are never passed in code: boto3 uses the default chain (AWS_PROFILE, environment
variables in CI, or the IAM role of the machine/container).
"""

import hashlib
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int
    sha256: str | None


class Storage(Protocol):
    def put_file(self, local_path: Path, key: str) -> None: ...
    def head(self, key: str) -> ObjectInfo | None: ...
    def list_keys(self, prefix: str = "") -> list[str]: ...
    def read_bytes(self, key: str) -> bytes: ...


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:  # reject ../ path traversal in keys
            raise ValueError(f"Key escapes the storage root: {key}")
        return path

    def put_file(self, local_path: Path, key: str) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_path, target)

    def head(self, key: str) -> ObjectInfo | None:
        path = self._path(key)
        return ObjectInfo(key, path.stat().st_size, sha256_of(path)) if path.exists() else None

    def list_keys(self, prefix: str = "") -> list[str]:
        base = self.root
        return sorted(
            p.relative_to(base).as_posix()
            for p in base.rglob("*")
            if p.is_file() and p.relative_to(base).as_posix().startswith(prefix)
        )

    def read_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()


class S3Storage:
    """S3 backend. Requires the optional `aws` extra: pip install -e ".[aws]"."""

    def __init__(self, bucket: str, *, region: str | None = None, client=None) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        # "standard" retry mode: exponential backoff with jitter on throttling and 5xx errors.
        self.client = client or boto3.client(
            "s3", region_name=region, config=Config(retries={"mode": "standard", "max_attempts": 5})
        )

    def put_file(self, local_path: Path, key: str) -> None:
        self.client.upload_file(
            str(local_path),
            self.bucket,
            key,
            ExtraArgs={
                "ServerSideEncryption": "AES256",  # SSE-S3: encrypted at rest, no KMS cost
                "Metadata": {"sha256": sha256_of(local_path)},
            },
        )

    def head(self, key: str) -> ObjectInfo | None:
        from botocore.exceptions import ClientError

        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return None
            raise  # AccessDenied etc. must surface, not look like "missing"
        return ObjectInfo(
            key, response["ContentLength"], response.get("Metadata", {}).get("sha256")
        )

    def list_keys(self, prefix: str = "") -> list[str]:
        keys: list[str] = []
        for page in self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=prefix
        ):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return sorted(keys)

    def read_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()


@dataclass
class SyncResult:
    uploaded: list[str]
    skipped: list[str]


def sync_directory(
    local_root: Path, prefix: str, storage: Storage, *, dry_run: bool = False
) -> SyncResult:
    """Upload every file under local_root/prefix whose content differs from the stored copy.

    Idempotent: re-running uploads nothing. A changed file (different sha256) is uploaded again.
    """
    result = SyncResult([], [])
    for path in sorted((local_root / prefix).rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue  # skip directories and temp files (".name.tmp" from atomic writes)
        key = path.relative_to(local_root).as_posix()
        existing = storage.head(key)
        if existing and existing.size == path.stat().st_size and existing.sha256 == sha256_of(path):
            result.skipped.append(key)
            continue
        if not dry_run:
            storage.put_file(path, key)
        result.uploaded.append(key)
        logger.info("%s %s", "would upload" if dry_run else "uploaded", key)
    return result
