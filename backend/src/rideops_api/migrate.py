"""Apply and inspect Lens metadata migrations (Alembic, schema `app`)."""

from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _config(conninfo: str | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    if conninfo:
        cfg.attributes["conninfo"] = conninfo
    return cfg


def upgrade(conninfo: str | None = None, revision: str = "head") -> None:
    command.upgrade(_config(conninfo), revision)


def downgrade(conninfo: str | None = None, revision: str = "base") -> None:
    command.downgrade(_config(conninfo), revision)


def head_revision() -> str | None:
    return ScriptDirectory.from_config(_config()).get_current_head()


def current_revision(conn: psycopg.Connection) -> str | None:
    """The applied revision, or None if Lens migrations have never run."""
    exists = conn.execute("SELECT to_regclass('app.alembic_version') IS NOT NULL").fetchone()
    if not exists or not next(iter(exists.values() if isinstance(exists, dict) else exists)):
        return None
    row = conn.execute("SELECT version_num FROM app.alembic_version").fetchone()
    if row is None:
        return None
    return row["version_num"] if isinstance(row, dict) else row[0]
