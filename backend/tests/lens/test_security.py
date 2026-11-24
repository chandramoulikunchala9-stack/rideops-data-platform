"""Unit tests: URL parser, Network Guard (SSRF corpus), envelope crypto, health scoring."""

import base64
import os

import pytest

from rideops_api.lens.connections import url_parser
from rideops_api.lens.security import netguard
from rideops_api.lens.security.crypto import DecryptError, KeyRing, KeyRingError, aad_for
from rideops_api.lens.status import scoring

# --------------------------------------------------------------------------- parser


def test_parse_postgres_url_moves_password_to_secret():
    p = url_parser.parse(
        "postgresql+psycopg://lens_ro:p%40ss%3Aword@DB.Example.COM.:6543/shop?sslmode=verify-full"
    )
    assert (p.engine, p.host, p.port, p.database, p.username) == (
        "postgres",
        "db.example.com",
        6543,
        "shop",
        "lens_ro",
    )
    assert p.password == "p@ss:word" and p.tls_mode == "verify-full"
    assert "p@ss" not in p.display_url and "pass" not in p.display_url
    assert p.public_fields()["has_password"] is True and "password" not in p.public_fields()


@pytest.mark.parametrize("url,engine,port,tls", [
    ("postgres://u@h/db", "postgres", 5432, "require"),
    ("postgresql://u@h/db?sslmode=prefer", "postgres", 5432, "require"),
    ("postgresql://u@h/db?sslmode=disable", "postgres", 5432, "disable"),
    ("mysql://u@h/db", "mysql", 3306, "require"),
    ("mariadb://u@h/db?ssl_mode=VERIFY_IDENTITY", "mysql", 3306, "verify-full"),
    ("mssql://u@h/db", "mssql", 1433, "verify-full"),
    ("sqlserver://u@h/db?TrustServerCertificate=yes", "mssql", 1433, "require"),
    ("mongodb+srv://u@cluster.example.net/app", "mongodb", 27017, "verify-full"),
])  # fmt: skip
def test_parse_engines(url, engine, port, tls):
    p = url_parser.parse(url)
    assert (p.engine, p.port, p.tls_mode) == (engine, port, tls)


@pytest.mark.parametrize("url,code", [
    ("", "URL_INVALID"),
    ("not a url", "URL_INVALID"),
    ("ftp://h/x", "ENGINE_UNSUPPORTED"),
    ("snowflake://acct/db", "ENGINE_UNSUPPORTED"),
    ("sqlite:///etc/passwd", "URL_INVALID"),
    ("postgresql://u@/db", "URL_INVALID"),
    ("postgresql://u@h:0/db", "URL_INVALID"),
    ("postgresql://u@h:70000/db", "URL_INVALID"),
    ("postgresql://u@h1,h2/db", "URL_INVALID"),
    ("postgresql://u@h/db?host=/var/run/postgresql", "OPTION_NOT_ALLOWED"),
    ("postgresql://u@h/db?sslrootcert=/etc/passwd", "OPTION_NOT_ALLOWED"),
    ("postgresql://u@h/db?sslkey=/root/.ssh/id_rsa", "OPTION_NOT_ALLOWED"),
    ("postgresql://u@h/db?options=-c%20default_transaction_read_only%3Doff", "OPTION_NOT_ALLOWED"),
    ("postgresql://u@h/db?passfile=/etc/shadow", "OPTION_NOT_ALLOWED"),
    ("mongodb+srv://u@c.example.net:27017/app", "URL_INVALID"),
])  # fmt: skip
def test_parse_rejects(url, code):
    with pytest.raises(url_parser.UrlError) as err:
        url_parser.parse(url)
    assert err.value.code == code


# ---------------------------------------------------------------- network guard


def fake(*ips):
    return lambda host, port: list(ips)


SSRF_CORPUS = [
    ("127.0.0.1", ["127.0.0.1"]), ("localhost", ["127.0.0.1", "::1"]), ("0.0.0.0", ["0.0.0.0"]),
    ("169.254.169.254", ["169.254.169.254"]), ("metadata.google.internal", ["169.254.169.254"]),
    ("10.0.0.5", ["10.0.0.5"]), ("172.16.3.4", ["172.16.3.4"]), ("172.31.255.255", ["172.31.255.255"]),
    ("192.168.1.1", ["192.168.1.1"]), ("100.64.0.1", ["100.64.0.1"]), ("224.0.0.1", ["224.0.0.1"]),
    ("255.255.255.255", ["255.255.255.255"]), ("::1", ["::1"]), ("fe80::1", ["fe80::1%eth0"]),
    ("fd00::1", ["fd00::1"]), ("::ffff:10.0.0.1", ["::ffff:10.0.0.1"]),
    ("::ffff:169.254.169.254", ["::ffff:169.254.169.254"]), ("2002:a00:1::1", ["2002:a00:1::1"]),
    ("2130706433", ["127.0.0.1"]), ("0x7f000001", ["127.0.0.1"]),
    ("rebind.example.com", ["93.184.216.34", "10.0.0.1"]),  # one private answer is enough to block
    ("internal.corp", ["192.168.10.10"]),
]  # fmt: skip


@pytest.mark.parametrize("host,answers", SSRF_CORPUS)
def test_ssrf_corpus_blocked(host, answers):
    with pytest.raises(netguard.NetworkPolicyError) as err:
        netguard.check(host, 5432, resolver=fake(*answers))
    assert err.value.code == "CONN_BLOCKED_NETWORK" and err.value.stage == "NETWORK_POLICY"


def test_ssrf_corpus_fully_blocked_rate():
    blocked = 0
    for host, answers in SSRF_CORPUS:
        try:
            netguard.check(host, 5432, resolver=fake(*answers))
        except netguard.NetworkPolicyError:
            blocked += 1
    assert blocked == len(SSRF_CORPUS)  # Phase 12 acceptance: 100 % blocked


def test_guard_public_pins_first_ip():
    r = netguard.check("db.example.com", 5432, resolver=fake("93.184.216.34", "93.184.216.35"))
    assert r.pinned_ip == "93.184.216.34" and not r.private_allowed


def test_guard_ports_dns_and_allowlists():
    with pytest.raises(netguard.NetworkPolicyError) as err:
        netguard.check("db.example.com", 22, resolver=fake("93.184.216.34"))
    assert err.value.code == "CONN_PORT_NOT_ALLOWED"
    assert netguard.check(
        "db.example.com", 2222, allowed_ports={2222}, resolver=fake("93.184.216.34")
    )

    def boom(host, port):
        raise OSError("nodename nor servname provided")

    with pytest.raises(netguard.NetworkPolicyError) as err:
        netguard.check("nope.invalid", 5432, resolver=boom)
    assert err.value.code == "CONN_DNS_FAILED" and err.value.stage == "DNS"
    # Workspace host allowlist and the self-hosted switch permit private targets.
    assert netguard.check(
        "db.internal", 5432, host_allowlist=["*.internal"], resolver=fake("10.0.0.9")
    ).private_allowed
    assert (
        netguard.check("x", 5432, allow_private_networks=True, resolver=fake("10.0.0.9")).pinned_ip
        == "10.0.0.9"
    )
    with pytest.raises(netguard.NetworkPolicyError):
        netguard.check(
            "evilinternal", 5432, host_allowlist=["*.internal"], resolver=fake("10.0.0.9")
        )
    with pytest.raises(netguard.NetworkPolicyError):
        netguard.check("/var/run/postgresql", 5432, resolver=fake("127.0.0.1"))


# ------------------------------------------------------------------------- crypto


def ring(active=2):
    return KeyRing({1: os.urandom(32), 2: os.urandom(32)}, active)


def test_envelope_roundtrip_and_aad_binding():
    kr = ring()
    secret = {"password": "s3cr3t-value", "ca_cert_pem": "-----BEGIN CERTIFICATE-----"}
    blob = kr.encrypt(secret, aad_for("ws1", "c1"))
    assert blob.kek_version == 2 and b"s3cr3t" not in blob.ciphertext + blob.wrapped_dek
    assert kr.decrypt(blob, aad_for("ws1", "c1")) == secret
    with pytest.raises(DecryptError):
        kr.decrypt(blob, aad_for("ws1", "c2"))  # copied to another connection
    with pytest.raises(DecryptError):
        kr.decrypt(blob, aad_for("ws2", "c1"))  # copied to another workspace
    tampered = blob.__class__(
        blob.kek_version, blob.wrapped_dek, blob.nonce, blob.ciphertext[:-1] + b"\0"
    )
    with pytest.raises(DecryptError):
        kr.decrypt(tampered, aad_for("ws1", "c1"))


def test_kek_rotation_rewraps_without_touching_data():
    k1, k2 = os.urandom(32), os.urandom(32)
    old = KeyRing({1: k1}, 1)
    blob = old.encrypt({"password": "x"}, b"a")
    new = KeyRing({1: k1, 2: k2}, 2)
    rewrapped = new.rewrap(blob, b"a")
    assert rewrapped.kek_version == 2 and rewrapped.ciphertext == blob.ciphertext
    assert KeyRing({2: k2}, 2).decrypt(rewrapped, b"a") == {
        "password": "x"
    }  # v1 can now be retired
    with pytest.raises(DecryptError):
        KeyRing({2: k2}, 2).decrypt(blob, b"a")


def test_keyring_config_errors_and_fingerprint():
    with pytest.raises(KeyRingError):
        KeyRing.from_env_value("")
    with pytest.raises(KeyRingError):
        KeyRing.from_env_value("1:" + base64.b64encode(b"short").decode())
    with pytest.raises(KeyRingError):
        KeyRing.from_env_value("1:not-base64!!")
    kr = KeyRing.from_env_value(f"1:{KeyRing.generate_key()},3:{KeyRing.generate_key()}")
    assert kr.active_version == 3 and kr.versions == [1, 3]
    a = kr.fingerprint({"host": "h", "secret": {"password": "a"}})
    assert a == kr.fingerprint({"secret": {"password": "a"}, "host": "h"})
    assert a != kr.fingerprint({"host": "h", "secret": {"password": "b"}})
    assert a != ring().fingerprint({"host": "h", "secret": {"password": "a"}})  # keyed


# ------------------------------------------------------------------------ scoring


def test_score_perfect_and_breakdown():
    assert scoring.score(
        {"latency_ms": 20, "connections.used_pct": 10, "cache.hit_ratio": 0.99}
    ) == (100, [])
    value, parts = scoring.score({"latency_ms": 2500, "connections.used_pct": 96, "cache.hit_ratio": 0.85,
                                  "replication.lag_s": 400, "queries.long_running": 5, "locks.blocked": 2,
                                  "deadlocks.delta": 1, "pg.xid_age_pct": 85, "storage.free_pct": 5},
                                 failed_in_last_10=7)  # fmt: skip
    assert value == 0
    by = {d.component: d.points for d in parts}
    assert by == {"latency": 25, "connections": 25, "cache": 15, "replication": 30, "long_queries": 15,
                  "blocking": 10, "deadlocks": 5, "failed_checks": 30, "wraparound": 40, "storage": 30}  # fmt: skip


def test_score_mid_thresholds():
    value, parts = scoring.score(
        {"latency_ms": 600, "connections.used_pct": 85, "cache.hit_ratio": 0.93}
    )
    assert value == 75 and {d.component for d in parts} == {"latency", "connections", "cache"}


@pytest.mark.parametrize("kwargs,state", [
    ({"reachable": True, "error_code": None, "error_stage": None, "health_score": 92}, "healthy"),
    ({"reachable": True, "error_code": None, "error_stage": None, "health_score": 60}, "degraded"),
    ({"reachable": True, "error_code": None, "error_stage": None, "health_score": 30}, "down"),
    ({"reachable": False, "error_code": "CONN_TIMEOUT", "error_stage": "TCP", "health_score": None}, "unreachable"),
    ({"reachable": False, "error_code": "CONN_DNS_FAILED", "error_stage": "DNS", "health_score": None}, "unreachable"),
    ({"reachable": False, "error_code": "CONN_AUTH_FAILED", "error_stage": "AUTH", "health_score": None},
     "auth_failed"),
    ({"reachable": False, "error_code": "CONN_TLS_VERIFY_FAILED", "error_stage": "TLS", "health_score": None},
     "tls_error"),
    ({"reachable": False, "error_code": "CONN_TOO_MANY", "error_stage": "AUTH", "health_score": None}, "down"),
    ({"reachable": True, "error_code": None, "error_stage": None, "health_score": 99, "paused": True}, "paused"),
])  # fmt: skip
def test_state_rules(kwargs, state):
    assert scoring.state_for(**kwargs) == state


def test_deltas_from_cumulative_counters():
    prev = {"deadlocks.total": 3, "tx.commits.total": 1000, "tx.rollbacks.total": 10}
    cur = {
        "deadlocks.total": 5,
        "tx.commits.total": 1600,
        "tx.rollbacks.total": 7,
    }  # rollbacks reset
    out = scoring.with_deltas(cur, prev, 60)
    assert (
        out["deadlocks.delta"] == 2
        and out["tx.commits_per_s"] == 10.0
        and "tx.rollbacks_per_s" not in out
    )
    assert "deadlocks.delta" not in scoring.with_deltas(cur, None, None)


def test_short_server_version():
    from rideops_api.lens.adapters.postgres import short_version

    assert short_version("16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)") == "PostgreSQL 16.15"
    assert short_version("17.5") == "PostgreSQL 17.5"
