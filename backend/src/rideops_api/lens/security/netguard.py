"""Network Guard (§10.2): SSRF and egress policy for every outbound connection to a target.

Resolve the host once, refuse private/internal/metadata addresses and non-allowlisted ports, and
return a *pinned* IP. Drivers then connect to that exact IP (PostgreSQL: `hostaddr`) while TLS
still verifies the original host name, so a DNS answer cannot change between check and connect
(DNS rebinding, ADR-7).
"""

import ipaddress
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass

DENIED_NETWORKS = [
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
        "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24", "192.168.0.0/16", "198.18.0.0/15",
        "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
        "::/128", "::1/128", "64:ff9b::/96", "100::/64", "2001:db8::/32", "fc00::/7", "fe80::/10", "ff00::/8",
    )
]  # fmt: skip

DEFAULT_ALLOWED_PORTS = frozenset({443, 1433, 1521, 3306, 5432, 6432, 27017})

Resolver = Callable[[str, int], list[str]]


class NetworkPolicyError(Exception):
    def __init__(self, code: str, status: int, message: str, stage: str) -> None:
        super().__init__(message)
        self.code, self.status, self.message, self.stage = code, status, message, stage


@dataclass(frozen=True)
class GuardResult:
    host: str
    port: int
    addresses: tuple[str, ...]
    pinned_ip: str
    private_allowed: bool


def system_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    seen: list[str] = []
    for *_, sockaddr in infos:
        ip = sockaddr[0]
        if ip not in seen:
            seen.append(ip)
    return seen


def is_denied(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%", 1)[0])  # drop IPv6 zone ids
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped  # ::ffff:10.0.0.1 is 10.0.0.1
    six_to_four = addr.sixtofour if isinstance(addr, ipaddress.IPv6Address) else None
    if six_to_four is not None and is_denied(str(six_to_four)):  # 2002::/16 wraps an IPv4 address
        return True
    return any(addr in net for net in DENIED_NETWORKS if net.version == addr.version) or (
        addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
        or addr.is_reserved or addr.is_unspecified
    )  # fmt: skip


def _host_allowlisted(host: str, allowlist: Iterable[str]) -> bool:
    host = host.lower().rstrip(".")
    for entry in allowlist:
        entry = entry.lower().strip().rstrip(".")
        if not entry:
            continue
        if entry.startswith("*."):
            if host.endswith(entry[1:]):
                return True
        elif host == entry:
            return True
    return False


def check(
    host: str,
    port: int,
    *,
    allow_private_networks: bool = False,
    allowed_ports: Iterable[int] = DEFAULT_ALLOWED_PORTS,
    host_allowlist: Iterable[str] = (),
    resolver: Resolver = system_resolver,
) -> GuardResult:
    if not host or "/" in host or host.startswith("@"):
        raise NetworkPolicyError("CONN_BLOCKED_NETWORK", 403, "Socket paths and local files aren't allowed.",
                                 "NETWORK_POLICY")  # fmt: skip
    if port not in set(allowed_ports):
        raise NetworkPolicyError("CONN_PORT_NOT_ALLOWED", 403,
                                 f"Port {port} is not allowed; ask an admin to add it.", "NETWORK_POLICY")  # fmt: skip
    try:
        addresses = resolver(host, port)
    except (OSError, UnicodeError) as exc:
        raise NetworkPolicyError(
            "CONN_DNS_FAILED", 400, f"Host name {host!r} not found.", "DNS"
        ) from exc
    if not addresses:
        raise NetworkPolicyError("CONN_DNS_FAILED", 400, f"Host name {host!r} not found.", "DNS")
    private_ok = allow_private_networks or _host_allowlisted(host, host_allowlist)
    if not private_ok:
        for ip in addresses:  # every answer must be safe, not just the first
            if is_denied(ip):
                raise NetworkPolicyError(
                    "CONN_BLOCKED_NETWORK", 403,
                    "Private/internal addresses aren't allowed. Use an SSH tunnel or the agent.", "NETWORK_POLICY",
                )  # fmt: skip
    return GuardResult(host=host, port=port, addresses=tuple(addresses), pinned_ip=addresses[0],
                       private_allowed=private_ok)  # fmt: skip
