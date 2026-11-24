# Runbook: Lens target database down

**Symptoms:** a "Database down" alert (critical) and an open incident; the connection shows
`down`, `unreachable`, `auth_failed` or `tls_error` on the Databases page.

## Triage (5 minutes)
1. Open the connection → **Overview**. The red banner shows the last `error_code`:

   | Code | Meaning | First action |
   |---|---|---|
   | `CONN_TIMEOUT`, `CONN_REFUSED` | Lens can't reach the host/port | Is the DB up? Is Lens's egress IP allowlisted (Databases page footer)? |
   | `CONN_DNS_FAILED` | host name doesn't resolve | Was the endpoint renamed (e.g. a new Neon branch)? |
   | `CONN_AUTH_FAILED` | credentials rejected | Password rotated or role dropped on the target? |
   | `CONN_DB_NOT_FOUND` | database missing / no CONNECT | Database renamed or grant revoked? |
   | `CONN_TLS_REQUIRED`, `CONN_TLS_VERIFY_FAILED` | TLS mismatch | Certificate changed or expired; check the TLS mode |
   | `CONN_TOO_MANY` | server out of connections | Look at the target's sessions; a pooler may be saturated |
   | `CONN_BLOCKED_NETWORK` | the Network Guard refused the address | DNS now points at a private IP: see [lens-ssrf-block](lens-ssrf-block.md) |
2. **Re-test** (connection page) for the stage-by-stage diagnosis without waiting for the next check.
3. Is it only this connection? If every connection failed at the same time, the problem is Lens
   itself (egress, DNS, worker): see [lens-worker-backlog](lens-worker-backlog.md).

## Resolve
* Credentials changed: **Rotate credentials** (admin) with the new password. Lens re-tests before saving.
* Planned work on the database: start a **maintenance window** on the connection's Alerts tab so the
  alert doesn't page during the work.
* Target genuinely down: follow the owning team's runbook; acknowledge the alert so others know.

## Verify
* The next check (or **Check now**) shows `healthy`/`degraded`; after **two** healthy checks the alert
  resolves on its own, a "resolved" notification goes out and the linked incident closes.

## Prevent
* Use a dedicated read-only role whose password isn't shared with applications.
* Rotate credentials in Lens in the same change that rotates them on the database.
