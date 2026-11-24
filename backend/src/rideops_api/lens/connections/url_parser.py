"""Connection URL parser (§10.1, FR-10/11).

The password is moved out of the URL into the secret; `display_url` never contains it.
Query parameters are kept only if they are on a per-engine allowlist: anything else is rejected
(e.g. `host=/var/run/postgresql` socket injection, `sslrootcert=/etc/passwd` local file reads,
`options=-c default_transaction_read_only=off`).
"""

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlsplit

SCHEMES: dict[str, str] = {
    "postgres": "postgres", "postgresql": "postgres", "postgresql+psycopg": "postgres",
    "postgresql+psycopg2": "postgres",
    "mysql": "mysql", "mariadb": "mysql", "mysql+pymysql": "mysql",
    "mssql": "mssql", "sqlserver": "mssql", "mssql+pyodbc": "mssql",
    "mongodb": "mongodb", "mongodb+srv": "mongodb",
    "sqlite": "sqlite",
    "snowflake": "snowflake", "bigquery": "bigquery", "databricks": "databricks",
}  # fmt: skip

CANONICAL_SCHEME = {
    "postgres": "postgresql",
    "mysql": "mysql",
    "mssql": "mssql",
    "mongodb": "mongodb",
}
DEFAULT_PORTS = {"postgres": 5432, "mysql": 3306, "mssql": 1433, "mongodb": 27017}

# Query parameters each engine may keep (lower-case). TLS parameters are consumed into tls_mode.
ALLOWED_OPTIONS: dict[str, frozenset[str]] = {
    "postgres": frozenset({"connect_timeout", "target_session_attrs"}),
    "mysql": frozenset({"charset", "connect_timeout"}),
    "mssql": frozenset({"applicationintent", "connect_timeout", "multisubnetfailover"}),
    "mongodb": frozenset(
        {
            "authsource",
            "authmechanism",
            "replicaset",
            "retrywrites",
            "w",
            "appname",
            "readpreference",
            "directconnection",
            "connecttimeoutms",
            "servers",
        }
    ),  # fmt: skip
}
TLS_PARAMS = frozenset({"sslmode", "ssl_mode", "ssl", "tls", "encrypt", "trustservercertificate"})
V2_ENGINES = frozenset({"snowflake", "bigquery", "databricks"})
TLS_MODES = ("disable", "require", "verify-ca", "verify-full")


class UrlError(Exception):
    def __init__(self, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


@dataclass
class ParsedUrl:
    engine: str
    host: str
    port: int
    database: str | None
    username: str | None
    password: str | None = field(repr=False)
    options: dict[str, str]
    tls_mode: str
    srv: bool = False

    @property
    def display_url(self) -> str:
        return build_display_url(self.engine, self.host, self.port, self.database, self.username,
                                 self.options, self.srv)  # fmt: skip

    def public_fields(self) -> dict[str, Any]:
        """Form fields for the UI. The password itself is never returned (only `has_password`)."""
        return {"engine": self.engine, "host": self.host, "port": self.port, "database": self.database,
                "username": self.username, "options": self.options, "tls_mode": self.tls_mode,
                "has_password": bool(self.password), "display_url": self.display_url}  # fmt: skip


def normalize_host(host: str) -> str:
    host = host.strip().lower().rstrip(".")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host:
        raise UrlError("URL_INVALID", "The URL has no host name.")
    if "/" in host or "\\" in host or host.startswith("@"):
        raise UrlError("URL_INVALID", "Socket paths and local files aren't allowed as hosts.")
    if any(c.isspace() for c in host):
        raise UrlError("URL_INVALID", "The host name contains spaces.")
    try:
        return host.encode("idna").decode("ascii") if not _looks_like_ip(host) else host
    except UnicodeError as exc:
        raise UrlError("URL_INVALID", f"Invalid host name {host!r}.") from exc


def _looks_like_ip(host: str) -> bool:
    import ipaddress

    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _tls_mode(engine: str, params: dict[str, str]) -> str:
    if engine == "postgres":
        mode = params.get("sslmode", "require")
        return {"disable": "disable", "allow": "require", "prefer": "require"}.get(mode, mode)
    if engine == "mysql":
        mode = params.get("ssl_mode", params.get("ssl", "required")).lower()
        return {
            "disabled": "disable",
            "false": "disable",
            "preferred": "require",
            "required": "require",
            "true": "require",
            "verify_ca": "verify-ca",
            "verify_identity": "verify-full",
        }.get(mode, mode)
    if engine == "mssql":
        if params.get("encrypt", "yes").lower() in {"no", "false", "optional"}:
            return "disable"
        trust = params.get("trustservercertificate", "no").lower() in {"yes", "true"}
        return "require" if trust else "verify-full"
    if engine == "mongodb":
        flag = params.get("tls", params.get("ssl"))
        return "disable" if flag is not None and flag.lower() == "false" else "verify-full"
    return "require"


def parse(url: str) -> ParsedUrl:
    url = (url or "").strip()
    if not url or "://" not in url or len(url) > 4096:
        raise UrlError(
            "URL_INVALID", "Can't parse URL. Check the format, e.g. postgresql://user@host:5432/db."
        )
    scheme = url.split("://", 1)[0].lower()
    engine = SCHEMES.get(scheme)
    if engine is None:
        raise UrlError("ENGINE_UNSUPPORTED", f"This database type ({scheme}) isn't supported yet.")
    if engine == "sqlite":
        raise UrlError(
            "URL_INVALID", "SQLite databases are added by uploading the file, not by URL."
        )
    if engine in V2_ENGINES:
        raise UrlError("ENGINE_UNSUPPORTED", f"{engine.title()} support is planned for v2.")

    parts = urlsplit(url)
    if parts.fragment:
        raise UrlError("URL_INVALID", "URLs with #fragments aren't valid connection strings.")
    netloc = parts.netloc
    userinfo, _, hostport = netloc.rpartition("@")
    if "," in hostport and engine != "mongodb":
        raise UrlError("URL_INVALID", "Multiple hosts aren't supported; use the primary's address.")
    username = unquote(userinfo.split(":", 1)[0]) if userinfo else None
    password = unquote(userinfo.split(":", 1)[1]) if userinfo and ":" in userinfo else None

    srv = scheme == "mongodb+srv"
    hosts = hostport.split(",")
    first = hosts[0]
    if first.startswith("["):
        host_part, _, rest = first[1:].partition("]")
        port_part = rest[1:] if rest.startswith(":") else ""
    else:
        host_part, _, port_part = first.partition(":")
    host = normalize_host(host_part)
    if port_part:
        if not port_part.isdigit() or not 0 < int(port_part) < 65536:
            raise UrlError("URL_INVALID", f"Invalid port {port_part!r}.")
        port = int(port_part)
    else:
        port = DEFAULT_PORTS[engine]
    if srv and port_part:
        raise UrlError("URL_INVALID", "mongodb+srv URLs can't include a port.")

    database = unquote(parts.path.lstrip("/")) or None
    if database and ("/" in database or "\0" in database):
        raise UrlError("URL_INVALID", "Invalid database name.")

    raw = parse_qsl(parts.query, keep_blank_values=True)
    params: dict[str, str] = {}
    for key, value in raw:
        params[key.lower()] = value
    rejected = sorted(k for k in params if k not in TLS_PARAMS and k not in ALLOWED_OPTIONS[engine])
    if rejected:
        raise UrlError("OPTION_NOT_ALLOWED", f"Option {rejected[0]!r} is not allowed for security reasons.",
                       {"options": rejected})  # fmt: skip
    if engine == "mongodb" and len(hosts) > 1:
        params["servers"] = ",".join(normalize_host(h.split(":")[0]) + (":" + h.split(":")[1] if ":" in h else "")
                                     for h in hosts[1:])  # fmt: skip
    tls_mode = _tls_mode(engine, params)
    if tls_mode not in TLS_MODES:
        raise UrlError("URL_INVALID", f"Unknown TLS mode {tls_mode!r}.")
    options = {k: v for k, v in params.items() if k not in TLS_PARAMS}
    if srv:
        options["srv"] = "true"  # set by the parser itself, never accepted from the query string
    return ParsedUrl(engine=engine, host=host, port=port, database=database, username=username,
                     password=password, options=options, tls_mode=tls_mode, srv=srv)  # fmt: skip


def build_display_url(
    engine: str,
    host: str | None,
    port: int | None,
    database: str | None,
    username: str | None,
    options: dict[str, Any] | None = None,
    srv: bool = False,
) -> str:
    options = dict(options or {})
    srv_flag = (
        str(options.pop("srv", "")).lower() == "true"
    )  # SRV shows in the scheme, not the query
    srv = srv or srv_flag
    scheme = CANONICAL_SCHEME.get(engine, engine) + ("+srv" if srv else "")
    user = f"{quote(username, safe='')}@" if username else ""
    shown_host = f"[{host}]" if host and ":" in host else (host or "")
    port_part = f":{port}" if port and not srv else ""
    db = f"/{quote(database, safe='')}" if database else ""
    query = "&".join(f"{k}={quote(str(v), safe=',')}" for k, v in sorted((options or {}).items()))
    return f"{scheme}://{user}{shown_host}{port_part}{db}" + (f"?{query}" if query else "")
