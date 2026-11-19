"""Local SparkSession with Delta Lake enabled.

On Databricks the platform provides `spark` (already Delta-enabled), so this module is only
used for local runs, tests and CI.
"""

import logging
import os

from delta import configure_spark_with_delta_pip
from pyspark.sql import SparkSession

logger = logging.getLogger(__name__)


def get_spark(app_name: str = "rideops", *, shuffle_partitions: int = 8) -> SparkSession:
    """Create (or reuse) a local Delta-enabled SparkSession.

    Settings chosen for a laptop rather than a cluster:
      * local[*]                      use all local cores
      * shuffle.partitions = 8        the default (200) creates hundreds of tiny tasks/files
      * session.timeZone = UTC        all timestamps are UTC; business dates convert explicitly
      * Delta SQL extension/catalog   enables MERGE, time travel, OPTIMIZE, DESCRIBE HISTORY
    """
    builder = (
        SparkSession.builder.appName(app_name)
        .master(os.environ.get("SPARK_MASTER", "local[*]"))
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog"
        )
        .config("spark.sql.shuffle.partitions", str(shuffle_partitions))
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.ui.showConsoleProgress", "false")
        .config("spark.driver.memory", os.environ.get("SPARK_DRIVER_MEMORY", "1g"))
        # Every new Delta table records row-level changes, which gold uses for incremental work.
        .config("spark.databricks.delta.properties.defaults.enableChangeDataFeed", "true")
        # Allow VACUUM demos with short retention; never do this in production.
        .config("spark.databricks.delta.retentionDurationCheck.enabled", "false")
    )
    spark = configure_spark_with_delta_pip(builder).getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    logger.info("Spark %s session '%s' ready", spark.version, app_name)
    return spark
