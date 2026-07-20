from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ObsConnectionState(StrEnum):
    DISCONNECTED = "Disconnected"
    CONNECTING = "Connecting"
    CONNECTED = "Connected"
    ERROR = "Error"


@dataclass(frozen=True, slots=True)
class ObsEvent:
    event_type: str
    event_data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ObsRequestResult:
    request_id: str
    request_type: str
    succeeded: bool
    code: int
    comment: str = ""
    response_data: dict[str, Any] = field(default_factory=dict)
