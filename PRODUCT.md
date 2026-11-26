# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Developers, data engineers and small data teams who own or look after a handful of databases
(PostgreSQL, MySQL / MariaDB, SQL Server, MongoDB, SQLite) and want to know, from one place, whether
each database is up and healthy, what is inside it, and how its data is changing. They sign up on
their own, connect a database by pasting a connection URL or filling in a form, and work in workspaces
with owner / admin / editor / viewer roles. Typical jobs: check a database is reachable and healthy in
seconds, understand an unfamiliar database fast, profile a table, run a safe read-only query, and get
told when a database goes down, slows down or a table stops updating.

Secondary audience: people evaluating the owner's data-engineering portfolio (interviewers,
reviewers), who click through the built-in demo database in a few minutes.

## Product Purpose

Lens is a data analytics and status platform for any database. Connect a database and Lens shows its
**status** (reachable, latency, version, uptime, size, sessions, locks, cache hit, replication and
other engine health metrics, tracked over time), its **statistics** (schemas, tables, row counts,
sizes, growth, freshness, index usage, column profiles) and its **analytics** (auto-generated
insights and charts, custom dashboards, read-only queries), and raises **alerts and incidents** when
metrics cross a threshold, resolving them when the database recovers.

Success: a user connects a database and within seconds can answer "is my database healthy?" and
"what is in it and how is it changing?" from the same screens, across every database they own.

## Positioning

- One view across engines: five database engines behind one adapter contract, so a PostgreSQL, a
  MongoDB and a SQLite file read the same way, with capabilities shown honestly per engine.
- Read-only by design, enforced in three layers (read-only sessions, a query guard, and engine
  permissions); Lens never writes to, migrates or tunes a user's database. It only recommends.
- Gentle on the target: catalog estimates, sampling, timeouts and concurrency caps instead of full
  scans.
- No agent to install: Lens connects by URL. It stores metadata and snapshots, never the user's data.
- A lightweight, unified view, not a full BI tool and not a replacement for vendor monitoring.

## Operating Context

- Main flow: sign up, create or join a workspace, add a connection (URL or form, test, save, first
  sync), then the connection's status, schema, tables, insights, query console, dashboards and
  alerts.
- Fleet overview compares every connection in a workspace, worst state first.
- The demo connection is a ride-hailing data warehouse (the former RideOps project): synthetic
  bookings, drivers, payments and pipeline data in PostgreSQL. It is sample data to explore, not the
  product's subject.
- Runs locally (Docker PostgreSQL 17, FastAPI, the React app at `/app`); the landing page is at `/`.
  Hosted deployment (free tiers) is prepared but not applied.

## Capabilities and Constraints

- REST API (FastAPI, `/api/v1`) with a metadata PostgreSQL storing users, workspaces, connections,
  snapshots, alerts and audit.
- Pages under `/app/lens`: fleet overview, add connection, connection status, schema explorer, table
  detail with profile, insights, query console, auto dashboard, dashboards, connection alerts, alerts
  and rules, incidents, notification channels, notifications; account pages (sign-up, login, password
  reset, email verification, invitations) at `/app/login` etc.
- Connection states: Healthy, Degraded, Down, Unreachable, Auth failed, TLS error, Unknown, Paused.
- Engine capabilities differ (for example, SQLite has no sessions, locks or replication); the UI says
  "not available for this engine" rather than showing empty data.
- Credentials are encrypted (per-connection keys) and never sent to the browser. TLS by default.
- Queries in the console are read-only and guarded (SQL, or `find` / `aggregate` JSON for MongoDB).
- The older ride-hailing pages (Overview, Business analytics, Source apps, Pipelines, Data quality,
  Monitoring, Cloud costs) remain in the codebase from the RideOps era; they are no longer the
  product. Decision (2026-11-26): they move under the demo connection, shown only when that
  connection is open, as proof of the reports Lens can produce; they leave the main navigation.
- Not in v1: Snowflake, BigQuery and Databricks SQL engines; a private-network agent; writing to
  user databases.
- Terminology: connection, target database, workspace, health check, status snapshot, schema sync,
  profile, insight, alert rule, alert, incident, notification channel, capability.

## Brand Commitments

- Name: Lens. "RideOps" survives only as the name of the demo warehouse and of the repository.
- The app must match the landing page (`frontend/index.html`): Familjen Grotesk with IBM Plex Mono,
  white ground, near-black ink #232323, mint accent #a1ffcb, sharp corners (user requirement).
- Imagery is data visuals drawn from the product's own data (status timelines, uptime bars,
  sparklines, schema and size visuals); no stock photography (user decision).

## Evidence on Hand

- Lens: 5 database engines behind one adapter contract (25/25 contract tests), SSRF test corpus
  100 % blocked, 0 axe-core accessibility violations on 14 pages.
- Design and architecture: `docs/ReContruction/lens-system-design.md`,
  `docs/ReContruction/lens-wireframes.md`; 5 Lens runbooks in `monitoring/runbooks/`.
- The demo warehouse is synthetic, generated from a fixed seed (10,000 customers, 2,000 drivers,
  100,000 bookings over 90 days, with defects planted on purpose) and must be labelled as demo data.
- There are no customers, testimonials, partners or production users. Never invent them, and never
  invent connection counts, uptime figures or benchmarks.

## Product Principles

1. Status first: the first thing on any connection is whether it is healthy, and since when.
2. Every figure says where and when it was read: which connection, which engine, how fresh.
3. Never touch the user's data: read-only, sampled and bounded, and the UI says so where it matters.
4. Honest about engines: a capability that an engine lacks is labelled, never faked or left blank.
5. Structured reports over loose widgets: each page reads top to bottom, summary first, then
   detail, then the exact table.

## Accessibility & Inclusion

- State is always icon + label + colour, never colour alone; charts offer a table view.
- Keyboard navigation, visible focus, WCAG AA contrast, light and dark themes.
