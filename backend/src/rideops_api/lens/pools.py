"""Per-target concurrency cap (§10.4): at most MAX_TARGET_SESSIONS sessions per connection at once.
No long-lived pools to customer databases: every operation connects, runs, and closes."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager

_lock = threading.Lock()
_slots: dict[str, threading.BoundedSemaphore] = {}


class TargetBusy(RuntimeError):
    pass


@contextmanager
def target_slot(connection_id: str, limit: int = 2, wait_s: float = 5.0) -> Iterator[None]:
    with _lock:
        sem = _slots.setdefault(str(connection_id), threading.BoundedSemaphore(limit))
    if not sem.acquire(timeout=wait_s):
        raise TargetBusy(f"Connection {connection_id} already has {limit} sessions in use")
    try:
        yield
    finally:
        sem.release()
