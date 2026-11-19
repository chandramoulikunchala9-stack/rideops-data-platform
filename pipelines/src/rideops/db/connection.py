"""PostgreSQL connections."""

import psycopg

from rideops.config import PostgresSettings
from rideops.utils.retry import retry


@retry(attempts=3, base_delay=1.0, retry_on=(psycopg.OperationalError,))
def connect(
    settings: PostgresSettings | None = None, *, dbname: str | None = None, autocommit: bool = False
) -> psycopg.Connection:
    """Open a connection, retrying briefly if the server is not reachable yet."""
    settings = settings or PostgresSettings()
    return psycopg.connect(settings.conninfo(dbname), autocommit=autocommit)
