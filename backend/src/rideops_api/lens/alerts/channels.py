"""Notification channels (F-AL-3, FR-42): encrypted config, delivery, signed webhooks."""

import hashlib
import hmac
import http.client
import json
import socket
import ssl
from typing import Any
from urllib.parse import urlsplit

from rideops_api.lens.security import netguard
from rideops_api.lens.security.crypto import KeyRing, SecretBlob
from rideops_api.notifications.email import Email, make_sender
from rideops_api.settings import LensSettings, allowed_port_set

TIMEOUT_S = 10


class DeliveryError(RuntimeError):
    pass


def aad(workspace_id: Any, channel_id: Any) -> bytes:
    return f"{workspace_id}|channel:{channel_id}".encode()


def encrypt_config(
    keyring: KeyRing, workspace_id: Any, channel_id: Any, config: dict[str, Any]
) -> SecretBlob:
    return keyring.encrypt(config, aad(workspace_id, channel_id))


def decrypt_config(keyring: KeyRing, row: dict[str, Any]) -> dict[str, Any]:
    if row["config_enc"] is None:
        return {}
    blob = SecretBlob(
        row["kek_version"], bytes(row["wrapped_dek"]), bytes(row["nonce"]), bytes(row["config_enc"])
    )
    return keyring.decrypt(blob, aad(row["workspace_id"], row["id"]))


def summarize(kind: str, config: dict[str, Any]) -> str:
    if kind in ("webhook", "slack"):
        parts = urlsplit(config.get("url", ""))
        return f"{parts.scheme}://{parts.hostname}/…"
    if kind == "email":
        return f"{len(config.get('emails', []))} recipient(s)"
    return "In-app notifications for workspace members"


def check_url(
    url: str, workspace_settings: dict[str, Any], settings: LensSettings
) -> netguard.GuardResult:
    """Webhook URLs get the same Network Guard as databases (§18.1 'Webhook abuse'): HTTPS only,
    no private/internal targets unless the operator or workspace explicitly allows them."""
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise DeliveryError("Webhook URLs must be absolute https:// URLs.")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    ports = (
        allowed_port_set(settings)
        | {443}
        | {int(p) for p in workspace_settings.get("allowed_ports", [])}
    )
    guard = netguard.check(
        parts.hostname, port,
        allow_private_networks=settings.allow_private_networks,
        allowed_ports=ports,
        host_allowlist=workspace_settings.get("egress_allowlist", []),
    )  # fmt: skip
    if parts.scheme == "http" and not guard.private_allowed:
        raise DeliveryError("Webhook URLs must use https://.")
    return guard


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class _PinnedHTTPS(http.client.HTTPSConnection):
    """Connect to the IP the Network Guard approved while verifying TLS for the original host."""

    def __init__(self, host: str, port: int, pinned_ip: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._pinned = pinned_ip

    def connect(self) -> None:
        sock = socket.create_connection((self._pinned, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, pinned_ip: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._pinned = pinned_ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._pinned, self.port), self.timeout)


def post_json(
    url: str, payload: dict[str, Any], guard: netguard.GuardResult, headers: dict[str, str]
) -> int:
    parts = urlsplit(url)
    body = json.dumps(payload, default=str).encode()
    cls = _PinnedHTTPS if parts.scheme == "https" else _PinnedHTTP
    conn = cls(parts.hostname, guard.port, guard.pinned_ip, TIMEOUT_S)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    try:
        conn.request("POST", path, body=body, headers={"Content-Type": "application/json",
                                                        "User-Agent": "rideops-lens", **headers})  # fmt: skip
        status = conn.getresponse().status
    except (OSError, http.client.HTTPException) as exc:
        raise DeliveryError(f"Delivery failed: {exc}") from exc
    finally:
        conn.close()
    if status >= 300:
        raise DeliveryError(f"Endpoint answered HTTP {status}")
    return status


def deliver(kind: str, config: dict[str, Any], payload: dict[str, Any], *, workspace_settings: dict[str, Any],
            settings: LensSettings) -> None:  # fmt: skip
    """Send one notification. Raises DeliveryError so the notify job retries with backoff."""
    if kind == "webhook":
        guard = check_url(config["url"], workspace_settings, settings)
        body = json.dumps(payload, default=str).encode()
        post_json(config["url"], payload, guard, {"X-Lens-Event": payload["event"],
                                                  "X-Lens-Signature": sign(config["secret"], body)})  # fmt: skip
    elif kind == "slack":
        guard = check_url(config["url"], workspace_settings, settings)
        icon = {"critical": ":red_circle:", "warning": ":large_orange_circle:"}.get(
            payload.get("severity"), ":white_circle:"
        )
        verb = {"alert.firing": "FIRING", "alert.resolved": "RESOLVED", "test": "TEST"}.get(
            payload["event"], payload["event"]
        )
        post_json(config["url"], {"text": f"{icon} *{verb}* {payload.get('summary', '')}\n{payload.get('link', '')}"},
                  guard, {})  # fmt: skip
    elif kind == "email":
        sender = make_sender(settings.email_backend)
        subject = f"[Lens {payload.get('severity', 'info').upper()}] {payload.get('summary', payload['event'])}"[
            :200
        ]
        for to in config.get("emails", []):
            sender.send(
                Email(
                    to=to,
                    subject=subject,
                    body=f"{payload.get('summary', '')}\n\n{payload.get('link', '')}\n",
                )
            )
    else:
        raise DeliveryError(f"Unknown channel kind {kind!r}")
