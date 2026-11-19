# Troubleshooting

Operational incidents (failed pipelines, schema drift, permissions, and so on) have runbooks in
[`monitoring/runbooks/`](../monitoring/runbooks/README.md). This page covers **development environment**
problems, all of them seen while building this project.

| Symptom | Cause | Fix |
|---|---|---|
| `failed to connect to the docker API ... dockerDesktopLinuxEngine` | Docker Desktop not running (it can also stop when the PC is low on memory) | Start Docker Desktop, wait for `docker info`, then `docker compose up -d postgres` |
| PostgreSQL tests are **skipped** with "connection timeout" | Database container not running | As above; check with `docker compose ps` |
| `docker compose up` errors with `Set POSTGRES_PASSWORD in .env` | No `.env` file, or the password is empty (intentional: there is no default) | `copy .env.example .env` and set a password |
| Spark jobs take minutes even for tiny data | Host RAM exhausted (8 GB laptop with 0.1 GB free, measured); the first JVM job also pays a one-off warm-up | Close heavy apps; keep `SPARK_DRIVER_MEMORY=1g`; run the full dataset in the background |
| `AssertionError ... _active_spark_context` when importing a module | A Spark `Column` was created at import time (no SparkSession yet) | Keep expressions as SQL strings or build them inside functions (`rideops.spark.quality`) |
| Delta `count()` still works after `VACUUM` | Delta answers `count()` from log statistics without reading files | Use `collect()` to prove files are gone |
| A late streaming event is not dropped | With several stateful operators, Spark filters late rows against the **previous** batch's watermark | Expect it; see the watermark test |
| API returns plain-text `Internal Server Error` | An exception inside an exception handler, e.g. `JSONResponse(404, content=...)`: the first positional argument is `content` | Use keyword arguments: `JSONResponse(status_code=..., content=...)` |
| API code changes not visible | uvicorn started without `--reload`, or an old process still holds port 8000 | Restart with `--reload`; find the port owner (`Get-NetTCPConnection -LocalPort 8000`) |
| A test passes alone but fails in the full suite | Test-isolation leak (e.g. a session-wide environment variable) | Scope `monkeypatch` to the fixture's setup |
| `write_text("a\nb")` content differs from expected bytes | Windows converts `\n` to `\r\n` in text mode | Use `write_bytes` in byte-exact tests |
| Frontend page tests time out | The first lazy import compiles Recharts slowly in jsdom | Raised `asyncUtilTimeout` in `src/test/setup.ts` |
| Page wider than the phone screen | A CSS grid `1fr`/`auto` track cannot shrink below its content's min-content width | Use `minmax(0, 1fr)` (see `app.css`) |
| Headless screenshot at 390px looks wrong | Desktop Edge/Chrome enforce a minimum viewport of about 500px (measured: `innerWidth=496`) | Render inside a 390px same-origin iframe |
| `typescript-eslint` peer dependency conflict | It supports TypeScript below 6.1 | TypeScript pinned to 6.0.3 |
| `terraform` not installed | Not needed: run it in Docker | `docker run --rm -v "$PWD/infra/terraform:/tf" -w /tf hashicorp/terraform:1.17.2 validate` |

Useful diagnostics:

```bash
docker compose ps                                    # containers and health
docker exec -it rideops-postgres psql -U rideops -d rideops
curl localhost:8000/api/v1/health                   # 200 = DB reachable, 503 = not
python -m rideops.db quality                         # all data-quality checks with status
python -m rideops.monitoring alerts                  # evaluate alert rules now
```
