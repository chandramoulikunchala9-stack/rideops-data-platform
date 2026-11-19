# pipelines/ — RideOps data platform (Python package `rideops`)

Everything that **produces and processes data**: source generators, ingestion, the SQL warehouse
pipeline, the Spark/Delta lakehouse, data-quality checks, monitoring and cost loaders.
The API in `../backend` uses this package as a library.

```
pipelines/
├── src/rideops/
│   ├── config.py            settings from environment variables / .env
│   ├── generators/          synthetic source data (10 datasets, deliberate defects)
│   ├── ingestion/           incremental landing → PostgreSQL loader (COPY, schema contracts)
│   ├── db/                  migrations, SQL ELT runner, named queries, pipeline runner
│   ├── quality/             SQL data-quality checks runner
│   ├── spark/               bronze / silver / gold, streaming, Delta demo, lakehouse DQ, export
│   ├── monitoring/          run tracking, alert rules, incidents, simulated incident
│   ├── costs/               AWS CUR 2.0 loader
│   ├── storage/             local / S3 storage backends
│   └── utils/               retry with backoff
├── sql/
│   ├── ddl/                 schema migrations (raw, core, ops, analytics, lakehouse)
│   ├── transform/           raw → core → analytics ELT
│   ├── analytics/           45 named analytical queries (9 scripts)
│   └── quality/             data-quality checks
├── tests/                   unit, integration (PostgreSQL), spark, fixtures
├── Dockerfile               Spark runtime image (Python 3.11 + Java 17 + PySpark + Delta)
└── pyproject.toml
```

## Commands (run from the repository root)

```bash
pip install -e "./pipelines[dev,aws]"
python -m rideops.generators --scale full      # synthetic sources → data/landing
python -m rideops.db all                       # migrate, load, transform, analytics, DQ
python -m rideops.monitoring alerts            # evaluate alert rules
python -m rideops.storage sync --dry-run       # landing → S3
python -m rideops.costs load-cur s3://bucket/cur2/
docker compose --profile spark run --rm spark python -m rideops.spark all
```

Tests: `pytest pipelines -m "not spark"` and
`docker compose --profile spark run --rm spark pytest pipelines/tests/spark`.
