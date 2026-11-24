# Runbook: Network Guard blocked a connection (SSRF protection)

**Symptoms:** a connection test or health check fails with `CONN_BLOCKED_NETWORK` or
`CONN_PORT_NOT_ALLOWED`; the audit log shows `network.blocked`; `/metrics` counts
`lens_ssrf_blocks_total`.

Lens refuses private, loopback, link-local (including cloud metadata `169.254.169.254`), CGNAT,
multicast, IPv4-mapped and 6to4 addresses, and ports outside `ALLOWED_PORTS` (design §10.2). This
stops anyone from using Lens to probe the network it runs in.

## Triage
1. Audit log (workspace settings or API) → `action=network` → who, which host/port, when.
2. Resolve the host yourself: does it point at a private address? (`dig +short db.example.com`)
3. Expected or not?
   * A user connecting a database that genuinely lives in a private network → **expected block**.
   * Repeated attempts at metadata/internal addresses → treat as a **security event**: review the
     user's other activity in the audit log and remove them from the workspace if it's abuse.
   * A public database whose DNS suddenly returns a private IP → DNS change or rebinding attempt;
     contact the database owner.

## Resolve (legitimate private databases)
* Workspace owner adds the host to the workspace **egress allowlist** (e.g. `db.internal` or
  `*.corp.example`) and, for a non-standard port, to **allowed ports**.
* Self-hosted Lens next to private databases: `ALLOW_PRIVATE_NETWORKS=true` (never on a shared,
  internet-facing deployment).
* Coming later: SSH tunnels (v1.x) and the outbound-only Lens agent (v2).

## Never
* Never allowlist `169.254.169.254`, `localhost` or the metadata hostnames on a hosted deployment.
