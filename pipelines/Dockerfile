# Spark runtime for the RideOps pipelines: Python + Java 17 + PySpark + Delta Lake.
# Running Spark in Linux avoids the Hadoop native-binary problems of Spark on Windows and
# matches the GitHub Actions runners. Built by docker compose (service "spark").
FROM python:3.11-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jre-headless procps \
    && rm -rf /var/lib/apt/lists/*
ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Editable install at /workspace/pipelines; compose mounts the repository over /workspace,
# so the container always runs the current source code.
WORKDIR /workspace/pipelines
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[dev,spark]"

# Download the Delta Lake JARs at build time so containers start without network access.
RUN python -c "from rideops.spark.session import get_spark; get_spark('warmup').stop()"

WORKDIR /workspace
CMD ["python", "-m", "rideops.spark", "--help"]
