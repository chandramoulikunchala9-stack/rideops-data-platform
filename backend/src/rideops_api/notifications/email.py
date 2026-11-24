"""Outgoing email: console (development), memory (tests), SMTP and Resend (production).

EMAIL_BACKEND=smtp   SMTP_URL=smtps://user:password@smtp.example.com:465  (or smtp://…:587 → STARTTLS)
EMAIL_BACKEND=resend RESEND_API_KEY=re_…
EMAIL_FROM="RideOps Lens <no-reply@your-domain>"  (must be a sender your provider has verified)
"""

import json
import logging
import smtplib
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

logger = logging.getLogger(__name__)
TIMEOUT_S = 15


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    def send(self, email: Email) -> None: ...


class EmailError(RuntimeError):
    pass


class ConsoleEmailSender:
    """Logs emails instead of sending them. Development only: bodies contain one-time links."""

    def send(self, email: Email) -> None:
        logger.warning(
            "DEV EMAIL (not sent) to=%s subject=%s\n%s", email.to, email.subject, email.body
        )


@dataclass
class MemoryEmailSender:
    outbox: list[Email] = field(default_factory=list)

    def send(self, email: Email) -> None:
        self.outbox.append(email)


class SmtpEmailSender:
    """smtps:// = implicit TLS (465); smtp:// = STARTTLS, required (587). Plain-text SMTP is refused."""

    def __init__(self, url: str, sender: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("smtp", "smtps") or not parts.hostname:
            raise EmailError(
                "SMTP_URL must look like smtps://user:password@host:465 or smtp://…:587"
            )
        self.implicit_tls = parts.scheme == "smtps"
        self.host, self.port = parts.hostname, parts.port or (465 if self.implicit_tls else 587)
        self.user = unquote(parts.username) if parts.username else None
        self.password = unquote(parts.password) if parts.password else None
        self.sender = sender

    def send(self, email: Email) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, email.to, email.subject
        msg.set_content(email.body)
        context = ssl.create_default_context()
        try:
            if self.implicit_tls:
                client = smtplib.SMTP_SSL(self.host, self.port, timeout=TIMEOUT_S, context=context)
            else:
                client = smtplib.SMTP(self.host, self.port, timeout=TIMEOUT_S)
            with client:
                if not self.implicit_tls:
                    client.starttls(
                        context=context
                    )  # never send credentials or links in clear text
                if self.user:
                    client.login(self.user, self.password or "")
                client.send_message(msg)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailError(f"SMTP delivery failed: {type(exc).__name__}: {exc}") from exc


class ResendEmailSender:
    API = "https://api.resend.com/emails"

    def __init__(self, api_key: str, sender: str, api_url: str | None = None) -> None:
        self.api_key, self.sender, self.api_url = api_key, sender, api_url or self.API

    def send(self, email: Email) -> None:
        body = json.dumps(
            {"from": self.sender, "to": [email.to], "subject": email.subject, "text": email.body}
        ).encode()
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                   "User-Agent": "rideops-lens"}  # fmt: skip
        req = urllib.request.Request(self.api_url, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 - fixed https API URL
                if r.status >= 300:
                    raise EmailError(f"Resend answered HTTP {r.status}")
        except urllib.error.HTTPError as exc:
            raise EmailError(f"Resend answered HTTP {exc.code}: {exc.read()[:200]!r}") from exc
        except OSError as exc:
            raise EmailError(f"Resend delivery failed: {exc}") from exc


_MEMORY = MemoryEmailSender()  # one shared outbox, so tests can read what workers sent


def make_sender(backend: str, settings: Any = None) -> EmailSender:
    if backend == "memory":
        return _MEMORY
    if backend == "smtp":
        url = (
            settings.smtp_url.get_secret_value()
            if settings is not None and settings.smtp_url
            else ""
        )
        return SmtpEmailSender(url, settings.email_from)
    if backend == "resend":
        key = (
            settings.resend_api_key.get_secret_value()
            if settings is not None and settings.resend_api_key
            else ""
        )
        if not key:
            raise EmailError("EMAIL_BACKEND=resend needs RESEND_API_KEY")
        return ResendEmailSender(
            key, settings.email_from, getattr(settings, "resend_api_url", None)
        )
    return ConsoleEmailSender()


def send_safely(sender: EmailSender, email: Email) -> bool:
    """Account emails must not break the request that triggered them (sign-up, reset, invite):
    a failure is logged, and the user can ask for the email again."""
    try:
        sender.send(email)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("Could not send email to %s (subject %r)", email.to, email.subject)
        return False
