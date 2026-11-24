# Runbook: Lens worker backlog / no checks running

**Symptoms:** "Checked … ago" keeps growing on every connection; `/metrics` shows
`lens_job_oldest_queued_seconds` > 300 or `lens_worker_last_activity_seconds` > 120.

## Triage
1. Scrape the metrics (token from the `INTERNAL_TOKEN` setting):
   ```bash
   curl -s -H "Authorization: Bearer $INTERNAL_TOKEN" https://<api>/metrics | grep -E "lens_job|lens_worker"
   ```
2. Is a worker running at all? With `LENS_ROLE=all` the API process runs it; on Render's free plan the
   instance **sleeps** when idle and nothing runs until a request wakes it.
3. Which job type is piling up?
   ```sql
   SELECT job_type, status, count(*), min(run_at), max(attempts)
     FROM app.jobs WHERE status IN ('queued', 'running') GROUP BY 1, 2 ORDER BY 3 DESC;
   ```

## Resolve
* Instance asleep: check the `lens-tick` GitHub Actions workflow is enabled and its secrets
  (`LENS_API_URL`, `LENS_INTERNAL_TOKEN`) are set; run it manually (*Run workflow*) or
  `curl -X POST -H "X-Internal-Token: …" https://<api>/internal/tick`.
* Worker crashed mid-job: jobs whose heartbeat is older than 2× their timeout are re-queued
  automatically by the scheduler's reaper; restart the service if no worker is alive.
* One slow target blocking everything: pause that connection (no job contacts a paused connection).
* Sustained load: run a separate worker (`python -m rideops_api worker`) next to the API
  (`LENS_ROLE=api`) and the scheduler.

## Verify
`lens_job_oldest_queued_seconds` drops below 60 and "Checked … ago" refreshes on the Databases page.
