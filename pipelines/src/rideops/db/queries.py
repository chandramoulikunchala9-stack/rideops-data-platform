"""Load named SQL queries from files.

A query starts with a `-- name: <query_name>` line and runs until the next one, so analysts can
keep several related queries in one readable .sql file while code refers to them by name.
"""

import re
from functools import lru_cache
from pathlib import Path

from rideops.config import SQL_DIR

_NAME_LINE = re.compile(r"^--\s*name:\s*(\w+)\s*$", re.MULTILINE)


def parse_named_queries(text: str) -> dict[str, str]:
    queries: dict[str, str] = {}
    matches = list(_NAME_LINE.finditer(text))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip().rstrip(";").strip()
        name = match.group(1)
        if name in queries:
            raise ValueError(f"Duplicate query name {name!r}")
        queries[name] = body
    return queries


@lru_cache
def load_queries(directory: Path = SQL_DIR / "analytics") -> dict[str, str]:
    """All named queries in a directory, keyed by name (names must be unique across files)."""
    queries: dict[str, str] = {}
    for path in sorted(directory.glob("*.sql")):
        for name, sql in parse_named_queries(path.read_text(encoding="utf-8")).items():
            if name in queries:
                raise ValueError(f"Duplicate query name {name!r} in {path.name}")
            queries[name] = sql
    return queries
