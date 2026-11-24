"""Outgoing email. Phase 11 ships a console sender (local development) and an in-memory
sender (tests). A real provider (SMTP / Resend) arrives with notification channels."""

import logging
from dataclasses import dataclass, field
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    def send(self, email: Email) -> None: ...


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


def make_sender(backend: str) -> EmailSender:
    return MemoryEmailSender() if backend == "memory" else ConsoleEmailSender()
