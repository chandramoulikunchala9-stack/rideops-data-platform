"""End-to-end: run the CLI exactly as a user would and check the files it produces."""

import json
from pathlib import Path

import pytest

from rideops.generators.__main__ import main


def _count_rows(path: Path) -> int:
    lines = path.read_text(encoding="utf-8").splitlines()
    return len(lines) - 1 if path.suffix == ".csv" else len(lines)  # CSV has a header row


def _run(tmp_path: Path, scale: str) -> dict:
    exit_code = main(
        ["--scale", scale, "--seed", "42", "--end-date", "2026-10-31", "--data-dir", str(tmp_path)]
    )
    assert exit_code == 0
    return json.loads(
        (tmp_path / "landing" / "_manifests" / "generation_0001.json").read_text(encoding="utf-8")
    )


def test_cli_writes_files_that_match_the_manifest(tmp_path: Path) -> None:
    manifest = _run(tmp_path, "small")

    assert set(manifest["entities"]) == {"vehicles", "drivers", "customers"}
    for name, info in manifest["entities"].items():
        path = tmp_path / info["file"]
        assert path.exists(), f"{name} file missing"
        assert _count_rows(path) == info["rows"]


def test_cli_rejects_unknown_scale(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):  # argparse exits with code 2 on bad arguments
        main(["--scale", "gigantic", "--data-dir", str(tmp_path)])


@pytest.mark.slow
def test_full_scale_meets_project_volume_targets(tmp_path: Path) -> None:
    manifest = _run(tmp_path, "full")
    entities = manifest["entities"]
    # rows = target + injected duplicates (1%)
    assert entities["customers"]["rows"] == 10_000 + 100
    assert entities["drivers"]["rows"] == 2_000 + 20
    assert entities["vehicles"]["rows"] == 1_000 + 10
