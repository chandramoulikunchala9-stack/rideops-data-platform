---
version: 1
slug: "frontend-src"
primary_target: "frontend/src"
related_targets: []
---

# Surface: RideOps dashboard (/app)

Mode: Operate. Audience: the data and operations team of a transport company (secondary: portfolio reviewers). Job: answer "how is the business doing" and "can I trust these numbers" from the same screens. Constraints: synthetic and simulated data labelled; validated chart palette; table view for every chart; tests stay green.

## Direction contract

THESIS: Every report reads top to bottom as a data story: Sources, then Checks, then Results. It refuses the KPI-tile grid that opens every dashboard and hides where the numbers came from.

OWN-WORLD: The landing page's world. White ground, ink #232323 sidebar band, mint #a1ffcb only for the active place and healthy confirmations, tinted fills for state, one colour per source application from the validated palette. Familjen Grotesk for everything, IBM Plex Mono only for table and file names. Sharp 6-8px corners, hairline borders, no shadows at rest.

STORY: The visitor sees which applications sent data and when, whether the data passed its checks and how much was quarantined, then the business results built from it, and drills into Source apps, Pipelines or Data quality from any band.

FIRST VIEWPORT: Ink sidebar left. Page header with title, period and date filter. Band "Sources": the source flow drawing (five applications, band width by rows, into validated tables and quarantine) at two thirds width, beside a freshness and delivery summary. The "Checks" band heading is visible below the fold line.

FORM: data story bands, position 7 of 7 on the ranked structure list; signature move: the source flow drawn from ops.ingestion_log. seed:9dd4804007e9003e

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
