"""Spark tests run where PySpark + Delta are installed (spark container, CI); skipped elsewhere."""

import pytest

pytest.importorskip("pyspark")
pytest.importorskip("delta")


@pytest.fixture(scope="session")
def spark():
    from rideops.spark.session import get_spark

    session = get_spark("rideops-tests", shuffle_partitions=2)
    yield session
    session.stop()


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests/spark" in item.nodeid.replace("\\", "/"):
            item.add_marker(pytest.mark.spark)
