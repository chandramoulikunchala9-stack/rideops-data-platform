"""SQL guard: layer 3 of read-only enforcement (§18.5, FR-34, §14.5).

The statement is parsed with sqlglot in the engine's dialect and accepted only if:
  * it is exactly one statement;
  * its root is SELECT / set operation (UNION, …) / WITH … SELECT, or EXPLAIN (no ANALYZE) / SHOW;
  * no node anywhere in the tree writes or changes state (INSERT/UPDATE/DELETE/MERGE in CTEs,
    SELECT INTO, FOR UPDATE/SHARE locks, DDL, COPY, SET, CALL, transactions, …);
  * no blocked function is called (sleeps, file/large-object access, dblink, backend control,
    advisory locks, sequence changes, …).
Anything sqlglot cannot parse is rejected. The read-only session (layer 2) and a read-only role
(layer 1) still back this up.
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

logging.getLogger("sqlglot").setLevel(logging.ERROR)  # unsupported syntax is rejected below anyway

DIALECTS = {"postgres": "postgres", "mysql": "mysql", "mssql": "tsql", "sqlite": "sqlite"}

BLOCKED_FUNCTIONS = {
    # sleeping / resource abuse
    "pg_sleep", "pg_sleep_for", "pg_sleep_until", "sleep", "benchmark", "waitfor", "generate_series_huge",
    # files and large objects
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file", "pg_ls_logdir", "pg_ls_waldir",
    "pg_ls_tmpdir", "pg_ls_archive_statusdir", "lo_import", "lo_export", "lo_get", "lo_put", "lo_create",
    "lo_unlink", "lo_open", "lo_from_bytea", "load_file", "openrowset", "opendatasource", "bulk",
    # other servers / OS
    "dblink", "dblink_exec", "dblink_connect", "dblink_open", "xp_cmdshell", "sp_executesql",
    "sys_exec", "sys_eval",
    # backend & server control
    "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf", "pg_rotate_logfile",
    "pg_promote", "pg_switch_wal", "pg_create_restore_point", "pg_start_backup", "pg_stop_backup",
    "pg_backup_start", "pg_backup_stop", "pg_drop_replication_slot", "pg_create_logical_replication_slot",
    "pg_create_physical_replication_slot", "pg_logical_slot_get_changes", "pg_replication_origin_create",
    # state changes
    "set_config", "nextval", "setval", "pg_advisory_lock", "pg_advisory_xact_lock",
    "pg_advisory_lock_shared", "pg_try_advisory_lock", "pg_try_advisory_xact_lock",
    "pg_notify", "txid_current", "pg_current_xact_id", "pg_import_system_collations",
    # query-to-anything helpers that execute arbitrary SQL text
    "query_to_xml", "query_to_xml_and_xmlschema", "query_to_xmlschema", "cursor_to_xml",
    "table_to_xml", "database_to_xml", "schema_to_xml",
}  # fmt: skip

WRITE_NODES: tuple[type[exp.Expression], ...] = tuple(
    t for t in (
        getattr(exp, name, None)
        for name in ("Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable", "TruncateTable",
                     "Copy", "Set", "Command", "Grant", "Revoke", "Transaction", "Commit", "Rollback", "Into",
                     "Lock", "LoadData", "Use", "Pragma", "Analyze", "Refresh", "Cache", "Uncache", "Execute",
                     "Call", "Declare", "Comment", "Describe", "Kill")
    ) if isinstance(t, type)
)  # fmt: skip

_EXPLAIN = re.compile(
    r"^\s*explain\b(?P<opts>\s*\([^)]*\)|(?:\s+(?:verbose|costs|format\s+\w+|analy[sz]e))*)\s*(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_SHOW = re.compile(r"^\s*show\s+[a-z_][a-z0-9_.]*\s*;?\s*$", re.IGNORECASE)
_SHOW_BLOCKED = re.compile(
    r"^\s*show\s+(all|.*(password|key|secret|hba|ident).*)\s*;?\s*$", re.IGNORECASE
)


class GuardRejected(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class Verdict:
    ok: bool
    kind: str | None = None  # select | explain | show
    code: str | None = None
    message: str | None = None
    tables: list[str] = field(default_factory=list)
    statement: str = ""

    @property
    def wrap(self) -> bool:
        """SELECTs get an outer LIMIT; EXPLAIN/SHOW run as they are."""
        return self.kind == "select"


def statement_hash(statement: str) -> str:
    return hashlib.sha256(" ".join(statement.split()).encode()).hexdigest()


def _function_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return (node.sql_name() if hasattr(node, "sql_name") else type(node).__name__).lower()


def _check_tree(tree: exp.Expression) -> None:
    for node in tree.walk():
        if isinstance(node, WRITE_NODES):
            kind = type(node).__name__.upper()
            if kind == "INTO":
                raise GuardRejected(
                    "QUERY_NOT_READONLY", "SELECT … INTO creates a table; only reads are allowed."
                )
            if kind == "LOCK":
                raise GuardRejected(
                    "QUERY_NOT_READONLY",
                    "FOR UPDATE/SHARE takes row locks; only plain reads are allowed.",
                )
            raise GuardRejected(
                "QUERY_NOT_READONLY", f"Only SELECT/WITH/EXPLAIN/SHOW are allowed (found {kind})."
            )
        if isinstance(node, exp.Func):
            name = _function_name(node)
            if name in BLOCKED_FUNCTIONS or name.startswith(("lo_", "pg_sleep", "dblink", "xp_")):
                raise GuardRejected("QUERY_FORBIDDEN_FUNCTION", f"Function {name} is blocked.")


# Objects a demo connection must never expose: Lens's own metadata schema (users, sessions,
# encrypted secrets) when it shares the RideOps database, and PostgreSQL role password tables.
DEMO_FORBIDDEN_SCHEMAS = frozenset({"app"})
DEMO_FORBIDDEN_TABLES = frozenset({"pg_authid", "pg_shadow", "pg_user_mapping", "pg_user_mappings"})


def validate(
    statement: str, engine: str = "postgres", *, max_length: int = 100_000, demo: bool = False
) -> Verdict:
    text = (statement or "").strip()
    try:
        if not text:
            raise GuardRejected("QUERY_EMPTY", "Type a query first.")
        if len(text) > max_length:
            raise GuardRejected(
                "QUERY_TOO_LARGE", f"Statements are limited to {max_length} characters."
            )
        dialect = DIALECTS.get(engine, "postgres")
        kind = "select"
        body = text
        if _SHOW.match(text):
            if _SHOW_BLOCKED.match(text):
                raise GuardRejected("QUERY_NOT_READONLY", "That SHOW command is not allowed.")
            return Verdict(ok=True, kind="show", statement=text.rstrip(";").strip())
        m = _EXPLAIN.match(text)
        if m:
            if re.search(r"\banaly[sz]e\b", m.group("opts") or "", re.IGNORECASE):
                raise GuardRejected(
                    "QUERY_NOT_READONLY",
                    "EXPLAIN ANALYZE executes the statement; use plain EXPLAIN.",
                )
            kind, body = "explain", m.group("rest")
        try:
            parsed = [p for p in sqlglot.parse(body, read=dialect) if p is not None]
        except ParseError as exc:
            raise GuardRejected(
                "QUERY_PARSE_ERROR", f"Can't parse this statement: {str(exc).splitlines()[0][:200]}"
            ) from None
        if len(parsed) != 1:
            raise GuardRejected("QUERY_MULTI_STATEMENT", "Run one statement at a time.")
        tree = parsed[0]
        if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)):
            raise GuardRejected("QUERY_NOT_READONLY", "Only SELECT/WITH/EXPLAIN/SHOW are allowed.")
        _check_tree(tree)
        refs = [t for t in tree.find_all(exp.Table) if t.name]
        if demo:
            for t in refs:
                if (
                    t.db or ""
                ).lower() in DEMO_FORBIDDEN_SCHEMAS or t.name.lower() in DEMO_FORBIDDEN_TABLES:
                    raise GuardRejected(
                        "QUERY_FORBIDDEN_OBJECT", "The demo connection can't read that object."
                    )
        tables = sorted({".".join(p for p in (t.db, t.name) if p) for t in refs})
        return Verdict(
            ok=True, kind=kind, tables=tables, statement=body.strip().rstrip(";").strip()
        )
    except GuardRejected as rej:
        return Verdict(ok=False, code=rej.code, message=rej.message)
