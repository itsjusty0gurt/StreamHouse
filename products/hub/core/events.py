from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from threading import RLock
from typing import Any, ClassVar

from shared.streamhouse_runtime.logger import Logger


EventCallback = Callable[..., None]


class Events:
    """
    Central event bus for Streamhouse Hub.

    Event activity is logged at DEBUG level so normal INFO logs
    do not become cluttered.
    """

    _listeners: ClassVar[
        dict[str, list[EventCallback]]
    ] = defaultdict(list)

    _lock: ClassVar[RLock] = RLock()

    @classmethod
    def subscribe(
        cls,
        event_name: str,
        callback: EventCallback,
    ) -> None:
        """Subscribe a callback to an event."""

        clean_event_name = cls._clean_event_name(event_name)

        with cls._lock:
            if callback in cls._listeners[clean_event_name]:
                Logger.debug(
                    (
                        f'Callback "{cls._callback_name(callback)}" is '
                        f'already subscribed to "{clean_event_name}".'
                    ),
                    source="EVENTS",
                )
                return

            cls._listeners[clean_event_name].append(callback)

        Logger.debug(
            (
                f'Subscribed "{cls._callback_name(callback)}" '
                f'to "{clean_event_name}".'
            ),
            source="EVENTS",
        )

    @classmethod
    def unsubscribe(
        cls,
        event_name: str,
        callback: EventCallback,
    ) -> bool:
        """
        Remove a callback from an event.

        Returns True if the callback was removed.
        """

        clean_event_name = cls._clean_event_name(event_name)

        with cls._lock:
            listeners = cls._listeners.get(clean_event_name)

            if not listeners or callback not in listeners:
                Logger.debug(
                    (
                        f'Callback "{cls._callback_name(callback)}" was '
                        f'not subscribed to "{clean_event_name}".'
                    ),
                    source="EVENTS",
                )
                return False

            listeners.remove(callback)

            if not listeners:
                cls._listeners.pop(clean_event_name, None)

        Logger.debug(
            (
                f'Unsubscribed "{cls._callback_name(callback)}" '
                f'from "{clean_event_name}".'
            ),
            source="EVENTS",
        )

        return True

    @classmethod
    def emit(
        cls,
        event_name: str,
        **event_data: Any,
    ) -> int:
        """
        Emit an event and call every subscribed callback.

        Returns the number of callbacks successfully called.
        """

        clean_event_name = cls._clean_event_name(event_name)

        with cls._lock:
            listeners = list(
                cls._listeners.get(clean_event_name, [])
            )

        if not listeners:
            return 0

        Logger.debug(
            (
                f'Emitting "{clean_event_name}" '
                f"to {len(listeners)} listener(s)."
            ),
            source="EVENTS",
        )

        successful_callbacks = 0

        for callback in listeners:
            try:
                callback(**event_data)
                successful_callbacks += 1

            except TypeError:
                Logger.exception(
                    (
                        f'Callback "{cls._callback_name(callback)}" '
                        f'received invalid data from '
                        f'"{clean_event_name}".'
                    ),
                    source="EVENTS",
                )

            except Exception:
                Logger.exception(
                    (
                        f'Callback "{cls._callback_name(callback)}" '
                        f'failed while handling '
                        f'"{clean_event_name}".'
                    ),
                    source="EVENTS",
                )

        return successful_callbacks

    @classmethod
    def clear(cls, event_name: str | None = None) -> int:
        """
        Remove listeners.

        Returns the number of listeners removed.
        """

        with cls._lock:
            if event_name is None:
                removed_count = sum(
                    len(listeners)
                    for listeners in cls._listeners.values()
                )

                cls._listeners.clear()

                Logger.debug(
                    f"Cleared {removed_count} event listener(s).",
                    source="EVENTS",
                )

                return removed_count

            clean_event_name = cls._clean_event_name(event_name)

            removed_listeners = cls._listeners.pop(
                clean_event_name,
                [],
            )

        removed_count = len(removed_listeners)

        Logger.debug(
            (
                f"Cleared {removed_count} listener(s) "
                f'from "{clean_event_name}".'
            ),
            source="EVENTS",
        )

        return removed_count

    @classmethod
    def listener_count(
        cls,
        event_name: str | None = None,
    ) -> int:
        """Return the listener count for one event or all events."""

        with cls._lock:
            if event_name is None:
                return sum(
                    len(listeners)
                    for listeners in cls._listeners.values()
                )

            clean_event_name = cls._clean_event_name(event_name)

            return len(
                cls._listeners.get(clean_event_name, [])
            )

    @staticmethod
    def _clean_event_name(event_name: str) -> str:
        """Normalize and validate an event name."""

        clean_event_name = event_name.strip().lower()

        if not clean_event_name:
            raise ValueError("Event name cannot be empty.")

        return clean_event_name

    @staticmethod
    def _callback_name(callback: EventCallback) -> str:
        """Return a useful name for a callback."""

        return getattr(
            callback,
            "__name__",
            callback.__class__.__name__,
        )
