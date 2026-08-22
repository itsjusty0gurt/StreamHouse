from __future__ import annotations

from enum import StrEnum
from typing import Callable
from urllib.error import URLError


class AIConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    VERIFYING = "verifying"
    READY = "ready"


def is_ai_transport_failure(error: BaseException) -> bool:
    """Return whether an error means the localhost AI service disappeared."""

    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(
            current,
            (ConnectionError, BrokenPipeError, TimeoutError, URLError),
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


class AIConnectionLifecycle:
    """Hub-owned authority for whether Streamhouse AI may receive requests."""

    def __init__(self, on_disconnect: Callable[[str], None] | None = None) -> None:
        self.state = AIConnectionState.DISCONNECTED
        self.generation = 0
        self.endpoint = ""
        self._on_disconnect = on_disconnect

    @property
    def ready(self) -> bool:
        return self.state is AIConnectionState.READY

    def begin_verification(self, endpoint: str) -> int:
        self.generation += 1
        self.endpoint = endpoint.rstrip("/")
        self.state = AIConnectionState.VERIFYING
        return self.generation

    def mark_ready(self, generation: int) -> bool:
        if (
            generation != self.generation
            or self.state is not AIConnectionState.VERIFYING
        ):
            return False
        self.state = AIConnectionState.READY
        return True

    def disconnect(self, reason: str = "") -> bool:
        was_connected = self.state is not AIConnectionState.DISCONNECTED
        self.generation += 1
        self.state = AIConnectionState.DISCONNECTED
        self.endpoint = ""
        if was_connected and self._on_disconnect is not None:
            self._on_disconnect(reason)
        return was_connected

    def transport_failed(self, error: BaseException) -> bool:
        if not is_ai_transport_failure(error):
            return False
        self.disconnect(str(error))
        return True
