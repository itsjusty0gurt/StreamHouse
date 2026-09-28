from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event, RLock


CancelCallback = Callable[[str], None]


class AutomationCancellation:
    """One cooperative cancellation signal for a root routine execution."""

    def __init__(self, queue_id: str = "", item_id: str = "") -> None:
        self.queue_id = queue_id
        self.item_id = item_id
        self.routine_stack: list[str] = []
        self._event = Event()
        self._reason = ""
        self._callbacks: dict[int, CancelCallback] = {}
        self._next_callback_id = 0
        self._lock = RLock()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason or "Cancelled by user."

    def cancel(self, reason: str = "Cancelled by user.") -> bool:
        clean_reason = str(reason).strip() or "Cancelled by user."
        with self._lock:
            if self._event.is_set():
                return False
            self._reason = clean_reason
            self._event.set()
            callbacks = tuple(self._callbacks.values())
            self._callbacks.clear()
        for callback in callbacks:
            try:
                callback(clean_reason)
            except Exception:
                # Cancellation must continue releasing every registered wait.
                continue
        return True

    def add_callback(self, callback: CancelCallback) -> Callable[[], None]:
        with self._lock:
            if self._event.is_set():
                reason = self._reason or "Cancelled by user."
                callback_id = -1
            else:
                callback_id = self._next_callback_id
                self._next_callback_id += 1
                self._callbacks[callback_id] = callback
                reason = ""
        if reason:
            callback(reason)

        def remove() -> None:
            if callback_id < 0:
                return
            with self._lock:
                self._callbacks.pop(callback_id, None)

        return remove


_CURRENT_CANCELLATION: ContextVar[AutomationCancellation | None] = ContextVar(
    "streamhouse_automation_cancellation",
    default=None,
)


def current_cancellation() -> AutomationCancellation | None:
    return _CURRENT_CANCELLATION.get()


@contextmanager
def cancellation_scope(
    cancellation: AutomationCancellation,
) -> Iterator[AutomationCancellation]:
    token = _CURRENT_CANCELLATION.set(cancellation)
    try:
        yield cancellation
    finally:
        _CURRENT_CANCELLATION.reset(token)
