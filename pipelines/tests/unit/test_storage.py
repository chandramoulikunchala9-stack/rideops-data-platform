"""Storage backends. S3 is exercised against moto (an in-process AWS mock): no real AWS calls."""

from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from rideops.storage.backends import LocalStorage, S3Storage, sync_directory

BUCKET = "rideops-test-raw"


@pytest.fixture
def landing(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    folder = root / "landing" / "bookings" / "batch_date=2026-10-31"
    folder.mkdir(parents=True)
    # bytes, not text: write_text would turn \n into \r\n on Windows
    (folder / "bookings_20261031_0001.csv").write_bytes(b"booking_id\nBKG-0000001\n")
    (folder / ".bookings_20261031_0002.csv.tmp").write_text("partial", "utf-8")  # must be ignored
    return root


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="ap-south-2")
        client.create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "ap-south-2"}
        )
        yield client


def test_s3_sync_uploads_once_with_encryption_and_checksum(landing: Path, s3) -> None:
    storage = S3Storage(BUCKET, client=s3)
    first = sync_directory(landing, "landing", storage)
    key = "landing/bookings/batch_date=2026-10-31/bookings_20261031_0001.csv"
    assert first.uploaded == [key]  # the hidden .tmp file was skipped

    head = s3.head_object(Bucket=BUCKET, Key=key)
    assert head["ServerSideEncryption"] == "AES256"
    assert len(head["Metadata"]["sha256"]) == 64

    second = sync_directory(landing, "landing", storage)
    assert second.uploaded == [] and second.skipped == [key]  # idempotent


def test_s3_sync_reuploads_changed_files(landing: Path, s3) -> None:
    storage = S3Storage(BUCKET, client=s3)
    sync_directory(landing, "landing", storage)
    file = next((landing / "landing").rglob("*.csv"))
    file.write_bytes(b"booking_id\nBKG-0000002\n")  # same size, different content
    assert len(sync_directory(landing, "landing", storage).uploaded) == 1
    assert storage.read_bytes(file.relative_to(landing).as_posix()).endswith(b"BKG-0000002\n")


def test_dry_run_uploads_nothing(landing: Path, s3) -> None:
    storage = S3Storage(BUCKET, client=s3)
    result = sync_directory(landing, "landing", storage, dry_run=True)
    assert len(result.uploaded) == 1
    assert storage.list_keys("landing/") == []


def test_head_of_missing_object_is_none_but_access_errors_raise(s3) -> None:
    storage = S3Storage(BUCKET, client=s3)
    assert storage.head("landing/nothing.csv") is None
    with pytest.raises(s3.exceptions.ClientError):
        S3Storage("bucket-that-does-not-exist", client=s3).head("x")


def test_local_storage_round_trip_and_path_traversal(landing: Path, tmp_path: Path) -> None:
    target = LocalStorage(tmp_path / "mirror")
    assert len(sync_directory(landing, "landing", target).uploaded) == 1
    assert target.list_keys("landing/bookings") == [
        "landing/bookings/batch_date=2026-10-31/bookings_20261031_0001.csv"
    ]
    with pytest.raises(ValueError):
        target.head("../outside.txt")
