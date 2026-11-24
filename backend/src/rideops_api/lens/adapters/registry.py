"""Engine → adapter class. Engines without an adapter yet report a clear error (§7.1)."""

from rideops_api.lens.adapters.base import DatabaseAdapter, Engine
from rideops_api.lens.adapters.postgres import PostgresAdapter

ADAPTERS: dict[str, type[DatabaseAdapter]] = {Engine.POSTGRES: PostgresAdapter}

# Shown by GET /meta/engines and the wizard's engine picker.
ENGINE_INFO: dict[str, dict] = {
    "postgres": {"label": "PostgreSQL", "default_port": 5432, "release": "v1",
                 "example": "postgresql://lens_ro@db.example.com:5432/shop?sslmode=verify-full"},
    "mysql": {"label": "MySQL / MariaDB", "default_port": 3306, "release": "v1",
              "example": "mysql://lens_ro@db.example.com:3306/shop"},
    "mssql": {"label": "SQL Server / Azure SQL", "default_port": 1433, "release": "v1",
              "example": "mssql://lens_ro@db.example.com:1433/shop"},
    "sqlite": {"label": "SQLite (file upload)", "default_port": None, "release": "v1", "example": None},
    "mongodb": {"label": "MongoDB / Atlas", "default_port": 27017, "release": "v1",
                "example": "mongodb+srv://lens_ro@cluster0.example.mongodb.net/shop"},
}  # fmt: skip


class AdapterUnavailable(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def get_adapter_class(engine: str) -> type[DatabaseAdapter]:
    cls = ADAPTERS.get(engine)
    if cls is None:
        if engine in ENGINE_INFO:
            raise AdapterUnavailable(
                "DRIVER_MISSING",
                f"{ENGINE_INFO[engine]['label']} support is not available on this server yet.",
            )
        raise AdapterUnavailable("ENGINE_UNSUPPORTED", "This database type isn't supported yet.")
    return cls


def available_engines() -> list[dict]:
    return [{"engine": e, **info, "available": e in ADAPTERS,
             "capabilities": sorted(ADAPTERS[e].capabilities) if e in ADAPTERS else []}
            for e, info in ENGINE_INFO.items()]  # fmt: skip
