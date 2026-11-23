# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The data and operations team of a transport (ride-hailing) company. They manage the data that arrives
from the company's different applications (the rider app, the driver app, the payments gateway, app
telemetry, and cloud billing) and use RideOps to see users, revenue and every related figure for their
products, and to know whether the pipelines feeding those figures are healthy.

RideOps is also the owner's data-engineering portfolio project, so people evaluating that work
(interviewers, reviewers) are a real secondary audience who click through it in a few minutes.

## Product Purpose

One place where a transport company's application data lands, is checked, and becomes trustworthy
reporting: bookings, completed rides, revenue by city, drivers, customers, cancellations, payments and
app funnels, next to the health of the pipelines, data-quality checks, alerts, incidents and cloud
spend that produced them. Success: a user can answer "how is the business doing" and "can I trust
these numbers" from the same screens.

## Positioning

Every reported figure traces back through its pipeline: landing files from each source application,
bronze/silver/gold layers, 35 data-quality checks, and a quarantine for rows that fail. The same gold
numbers are computed twice, in PostgreSQL SQL and in Spark, and reconcile exactly (0 of 540 date x city
cells differ). Business reporting and data-platform health share one product rather than living in
separate tools.

## Operating Context

- Source applications and their data: rider app (bookings, customers), driver app (drivers, vehicles),
  payments gateway (payments), app telemetry (app events: sessions, fare views, bookings, crashes by
  platform android / ios / web; ride event streams), cloud billing (AWS cost report format), and the
  platform's own pipeline runs.
- Six cities: Bengaluru, Mumbai, Hyderabad, Delhi, Chennai, Pune. Money in INR; cloud cost in USD.
- Runs locally (Docker Postgres 17, a Spark 4.2 container, FastAPI, the React dashboard at `/app`);
  the landing page is at `/`. Hosted and cloud deployments are prepared but not applied.

## Capabilities and Constraints

- REST API (FastAPI, `/api/v1`) over PostgreSQL schemas raw, core, analytics, ops and lakehouse.
- Dashboard pages: Overview, Business analytics, Pipelines and run detail, Data quality, Alerts and
  incidents, Cloud costs, Settings and access.
- Retrying a pipeline from the UI is limited to whitelisted local jobs and disabled by default.
- Terminology: bookings, completed rides, cancellations, no driver found, quarantine, data-quality
  check (PASS / WARN / FAIL), pipeline run, layer (bronze, silver, gold, serving), incident, alert,
  simulated cost.

## Brand Commitments

- Name: RideOps.
- The dashboard must match the landing page (`frontend/index.html`): Familjen Grotesk with IBM Plex
  Mono, white ground, near-black ink #232323, mint accent #a1ffcb, sharp corners (user requirement).
- Imagery is data visuals drawn from the product's own data (maps of the six cities, pipeline flow
  diagrams, sparklines, status visuals); no stock photography (user decision).

## Evidence on Hand

- All business data is synthetic, generated from a fixed seed (10,000 customers, 2,000 drivers,
  1,000 vehicles, 100,000 bookings over 90 days, with defects planted on purpose).
- Cloud costs are simulated and must always be labelled simulated.
- Real measured results: SQL vs Spark reconciliation 0 of 540; 35 data-quality checks; 14 pipelines;
  4 documented incidents with postmortems (`monitoring/incidents/`); 8 runbooks
  (`monitoring/runbooks/`); 153 automated tests.
- There are no customers, testimonials, partners or production users. Never invent them.

## Product Principles

1. Every number shows where it came from: its source application, layer and freshness.
2. Business figures and data-platform health sit side by side; trust is part of the report.
3. Synthetic and simulated data is labelled where it appears, never hidden.
4. Structured reports over loose widgets: each page reads as a report with a summary first, then
   detail, then the exact table.

## Accessibility & Inclusion

- Charts use the validated categorical palette and always offer a table view; identity never relies on
  colour alone (legends, labels, icons with status).
- Keyboard navigation, visible focus, WCAG AA contrast, light and dark themes.
