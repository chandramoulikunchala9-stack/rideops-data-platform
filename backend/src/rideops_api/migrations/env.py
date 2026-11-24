"""Alembic environment. Migrations are plain SQL (op.execute); no ORM models.

The connection string is passed programmatically by `rideops_api.migrate`
(config attribute `conninfo`), so no password ever lands in alembic.ini.
"""

import psycopg
from alembic import context
from sqlalchemy import create_engine, pool

from rideops_api.settings import get_lens_settings

config = context.config
conninfo = config.attributes.get("conninfo") or get_lens_settings().metadata_conninfo()

engine = create_engine(
    "postgresql+psycopg://", creator=lambda: psycopg.connect(conninfo), poolclass=pool.NullPool
)
with engine.connect() as connection:
    context.configure(
        connection=connection,
        target_metadata=None,
        version_table="alembic_version",
        version_table_schema="app",
        include_schemas=True,
    )
    connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS app")
    connection.commit()
    with context.begin_transaction():
        context.run_migrations()
