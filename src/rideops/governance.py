"""Role model shared by the Settings & Access page and docs/security.md.

Mirrors databricks/unity_catalog/02_roles_and_grants.sql and the IAM policies in
infra/terraform. Users listed here are FICTIONAL demo accounts.
"""

ROLES: list[dict] = [
    {
        "name": "Platform Administrator",
        "group": "rideops-platform-admins",
        "description": "Owns the catalog, infrastructure and access policies.",
        "permissions": [
            "ALL PRIVILEGES on catalog rideops_<env>",
            "Manage grants, clusters, jobs and cluster policies",
            "Apply Terraform (S3 buckets, IAM roles)",
            "Read cost and billing data",
        ],
    },
    {
        "name": "Data Engineer",
        "group": "rideops-data-engineers",
        "description": "Builds and operates pipelines.",
        "permissions": [
            "SELECT, MODIFY, CREATE TABLE on bronze, silver, gold, ops",
            "READ VOLUME landing.raw; READ/WRITE VOLUME landing.checkpoints",
            "Run and retry jobs; view job and query history",
            "Sees unmasked PII (email, phone)",
        ],
    },
    {
        "name": "Data Analyst",
        "group": "rideops-data-analysts",
        "description": "Builds reports and ad-hoc analysis.",
        "permissions": [
            "SELECT on gold",
            "SELECT on silver.bookings and silver.customers (PII masked)",
            "Row filter: only cities granted in ops.analyst_city_access",
        ],
    },
    {
        "name": "Read-only User",
        "group": "rideops-readonly",
        "description": "Consumes dashboards.",
        "permissions": ["SELECT on gold", "View dashboards"],
    },
]

DEMO_USERS: list[dict] = [
    {"name": "Asha Platform", "email": "asha.admin@example.com", "role": "Platform Administrator"},
    {"name": "Ravi Engineer", "email": "ravi.de@example.com", "role": "Data Engineer"},
    {"name": "Meera Engineer", "email": "meera.de@example.com", "role": "Data Engineer"},
    {"name": "Kiran Analyst", "email": "kiran.analyst@example.com", "role": "Data Analyst"},
    {"name": "Sam Viewer", "email": "sam.viewer@example.com", "role": "Read-only User"},
]
