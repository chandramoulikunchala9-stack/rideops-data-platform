"""Static validation of the Databricks Asset Bundle (no workspace needed).

`databricks bundle validate` needs a workspace login; these checks catch the most common
mistakes locally and in CI: broken YAML, wrong notebook paths, bad task dependencies,
schedules accidentally running in dev, and notebooks that are not valid Python.
"""

import ast
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3] / "databricks"  # pipelines/tests/unit -> repo root


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def bundle() -> dict:
    merged = _load(ROOT / "databricks.yml")
    merged["resources"] = {"jobs": {}, "pipelines": {}}
    for path in sorted((ROOT / "resources").glob("*.yml")):
        for kind, items in _load(path)["resources"].items():
            merged["resources"].setdefault(kind, {}).update(items)
    return merged


def test_bundle_has_dev_and_prod_targets(bundle) -> None:
    assert set(bundle["targets"]) == {"dev", "prod"}
    assert bundle["targets"]["dev"]["mode"] == "development"
    assert bundle["targets"]["prod"]["mode"] == "production"
    assert "run_as" in bundle["targets"]["prod"]  # prod runs as a service principal


def test_dev_schedules_are_paused_by_default(bundle) -> None:
    assert bundle["variables"]["schedule_pause_status"]["default"] == "PAUSED"
    for job in bundle["resources"]["jobs"].values():
        if "schedule" in job:
            assert job["schedule"]["pause_status"] == "${var.schedule_pause_status}"


def test_every_referenced_file_exists(bundle) -> None:
    for name, job in bundle["resources"]["jobs"].items():
        for task in job["tasks"]:
            if "notebook_task" in task:
                path = (ROOT / "resources" / task["notebook_task"]["notebook_path"]).resolve()
                assert path.exists(), f"{name}.{task['task_key']}: {path} missing"
    for pipeline in bundle["resources"]["pipelines"].values():
        for lib in pipeline["libraries"]:
            assert (ROOT / "resources" / lib["file"]["path"]).resolve().exists()


def _assert_acyclic(job: str, deps: dict[str, list[str]]) -> None:
    """Depth-first search; reaching a task that is still being visited means a cycle."""
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(task: str) -> None:
        assert task not in visiting, f"cycle in {job} at {task}"
        if task in done:
            return
        visiting.add(task)
        for upstream in deps[task]:
            visit(upstream)
        visiting.discard(task)
        done.add(task)

    for task in deps:
        visit(task)


def test_task_dependencies_form_a_valid_dag(bundle) -> None:
    for name, job in bundle["resources"]["jobs"].items():
        keys = {t["task_key"] for t in job["tasks"]}
        deps = {
            t["task_key"]: [d["task_key"] for d in t.get("depends_on", [])] for t in job["tasks"]
        }
        for task, upstream in deps.items():
            assert set(upstream) <= keys, f"{name}.{task} depends on an unknown task"
        _assert_acyclic(name, deps)


def test_daily_job_runs_quality_gate_before_gold(bundle) -> None:
    tasks = {t["task_key"]: t for t in bundle["resources"]["jobs"]["rideops_daily"]["tasks"]}
    assert [d["task_key"] for d in tasks["gold_aggregations"]["depends_on"]] == [
        "data_quality_gate"
    ]


def test_scheduled_jobs_alert_on_failure_and_limit_concurrency(bundle) -> None:
    for name, job in bundle["resources"]["jobs"].items():
        if "schedule" in job:
            assert job["email_notifications"]["on_failure"], name
            assert job["max_concurrent_runs"] == 1, name


@pytest.mark.parametrize(
    "path", sorted([*ROOT.glob("notebooks/*.py"), *ROOT.glob("dlt/*.py")]), ids=lambda p: p.name
)
def test_notebooks_are_valid_python(path: Path) -> None:
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_cluster_policy_is_valid_json() -> None:
    import json

    policy = json.loads((ROOT / "policies" / "job_cluster_policy.json").read_text(encoding="utf-8"))
    assert policy["custom_tags.project"]["value"] == "rideops"
