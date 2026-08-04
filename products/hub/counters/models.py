from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any, Mapping

COUNTER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SCOPES = ("channel_total", "stream_total", "viewer_total", "viewer_stream_total")
READ_SCOPES = (*SCOPES, "viewer_rank")


def validate_counter_id(value: str) -> str:
    counter_id = value.strip().casefold()
    if not COUNTER_ID_PATTERN.fullmatch(counter_id):
        raise ValueError("Counter IDs must start with a letter and contain only lowercase letters, numbers, and underscores (maximum 64 characters).")
    return counter_id


def counter_id_from_name(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "_", value.strip().casefold()).strip("_")
    if not result or not result[0].isalpha():
        result = f"counter_{result}" if result else "counter"
    return validate_counter_id(result[:64].rstrip("_"))


@dataclass(frozen=True, slots=True)
class CounterDefinition:
    counter_id: str
    display_name: str
    singular: str
    plural: str
    enabled: bool = True
    track_channel_total: bool = True
    track_stream_total: bool = True
    track_viewer_total: bool = True
    track_viewer_stream_total: bool = False
    exclude_known_bots: bool = True
    allow_negative: bool = False
    minimum: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "counter_id", validate_counter_id(self.counter_id))
        name = self.display_name.strip()
        if not name:
            raise ValueError("Counter name is required.")
        object.__setattr__(self, "display_name", name)
        object.__setattr__(self, "singular", self.singular.strip() or name)
        object.__setattr__(self, "plural", self.plural.strip() or name)
        object.__setattr__(self, "minimum", int(self.minimum))
        if not any(self.tracks(scope) for scope in SCOPES):
            raise ValueError("Select at least one tracked counter scope.")
        if not self.allow_negative and self.minimum < 0:
            raise ValueError("Minimum cannot be negative unless negative values are allowed.")

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "CounterDefinition":
        return cls(
            counter_id=str(values.get("counter_id", "")), display_name=str(values.get("display_name", "")),
            singular=str(values.get("singular", "")), plural=str(values.get("plural", "")),
            enabled=bool(values.get("enabled", True)), track_channel_total=bool(values.get("track_channel_total", True)),
            track_stream_total=bool(values.get("track_stream_total", True)), track_viewer_total=bool(values.get("track_viewer_total", True)),
            track_viewer_stream_total=bool(values.get("track_viewer_stream_total", False)), exclude_known_bots=bool(values.get("exclude_known_bots", True)),
            allow_negative=bool(values.get("allow_negative", False)), minimum=int(values.get("minimum", 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    def updated(self, **changes: Any) -> "CounterDefinition":
        changes.pop("counter_id", None)
        return replace(self, **changes)

    def tracks(self, scope: str) -> bool:
        return bool(getattr(self, f"track_{scope}", False))


@dataclass(frozen=True, slots=True)
class CounterValues:
    channel_total: int = 0
    stream_total: int = 0
    viewer_total: int = 0
    viewer_stream_total: int = 0
    viewer_rank: int = 0
    viewer_display_name: str = ""
    viewer_login: str = ""
