"""Importing this package registers every job handler."""

from rideops_api.jobs.tasks import maintenance  # noqa: F401
from rideops_api.lens.status import health  # noqa: F401 - health_check
