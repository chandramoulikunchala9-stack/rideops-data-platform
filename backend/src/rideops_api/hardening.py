"""Cross-cutting hardening (Phase 15): log redaction, security headers, a per-user rate limit and
in-process request metrics.

The rate limiter and counters are per process. That matches the free-tier deployment (one
`LENS_ROLE=all` process); behind several API replicas each one enforces its own share.
"""

import hashlib
import logging
import re
import threading
import time
from collections import defaultdict, deque

# ------------------------------------------------------------------ log redaction
_PATTERNS = [
    # scheme://user:password@host  → scheme://user:***@host
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)([^\s@/]+)(@)", re.IGNORECASE), r"\1***\3"),
    # password=..., pwd=..., secret: ..., token=..., api_key=...  (conninfo strings, dicts, JSON)
    (
        re.compile(
            r"""(\b(?:password|passwd|pwd|secret|token|api[_-]?key|signing_secret)\b["']?\s*[=:]\s*["']?)([^\s"',&}]+)""",
            re.IGNORECASE,
        ),
        r"\1***",
    ),  # fmt: skip
    # Authorization / cookie headers
    (
        re.compile(
            # the whole header value: "Bearer <token>" and cookie lists contain spaces
            r"""(\b(?:authorization|cookie|x-csrf-token|x-internal-token)\b\s*[:=]\s*)([^\r\n,"']+)""",
            re.IGNORECASE,
        ),
        r"\1***",
    ),
]


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    """Masks credentials in every log record (§18.4: never logged), including exception text."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a broken format string must not drop the record
            return True
        clean = redact(message)
        if clean != message:
            record.msg, record.args = clean, None
        if record.exc_info and not record.exc_text:
            formatter = logging.Formatter()
            record.exc_text = redact(formatter.formatException(record.exc_info))
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


def install_log_redaction() -> None:
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())


# --------------------------------------------------------------- security headers
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


def security_headers(path: str, *, hsts: bool) -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "same-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Cross-Origin-Opener-Policy": "same-origin",
    }
    if not path.startswith(DOCS_PATHS):  # Swagger UI loads its own scripts; JSON needs nothing
        headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    if hsts:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers


# -------------------------------------------------------------------- rate limits
class SlidingWindowLimiter:
    def __init__(self, limit: int, window_s: float = 60.0) -> None:
        self.limit, self.window_s = limit, window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, now: float | None = None) -> tuple[bool, int]:
        """Record one request. Returns (allowed, retry_after_s)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - self.window_s:
                q.popleft()
            if len(q) >= self.limit:
                return False, max(1, int(q[0] + self.window_s - now) + 1)
            q.append(now)
            if len(self._hits) > 50_000:  # bound memory: forget idle keys
                for k in [k for k, v in self._hits.items() if not v][:10_000]:
                    del self._hits[k]
            return True, 0


def client_key(cookie_value: str | None, ip: str | None) -> str:
    if cookie_value:
        return "s:" + hashlib.sha256(cookie_value.encode()).hexdigest()[:24]
    return f"ip:{ip or 'unknown'}"


# ------------------------------------------------------------------- API metrics
class RequestMetrics:
    def __init__(self) -> None:
        self._counts: dict[tuple[str, int], int] = defaultdict(int)
        self._lock = threading.Lock()

    def observe(self, route: str, status: int) -> None:
        with self._lock:
            self._counts[(route, status)] += 1

    def snapshot(self) -> dict[tuple[str, int], int]:
        with self._lock:
            return dict(self._counts)


METRICS = RequestMetrics()
