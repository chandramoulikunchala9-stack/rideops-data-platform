"""Importing this package registers every job handler."""

from rideops_api.jobs.tasks import maintenance  # noqa: F401
from rideops_api.lens.catalog import service as catalog  # noqa: F401 - schema_sync, stats_refresh
from rideops_api.lens.profiling import service as profiling  # noqa: F401 - profile_table
from rideops_api.lens.queries import service as queries  # noqa: F401 - dashboards_due
from rideops_api.lens.status import health  # noqa: F401 - health_check
