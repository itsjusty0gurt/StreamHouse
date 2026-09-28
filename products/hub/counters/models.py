from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping

COUNTER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SCOPES = ("channel_total", "stream_total", "viewer_total", "viewer_stream_total")
NUMERIC_TYPES = ("integer", "decimal")


def parse_counter_number(value: Any, numeric_type: str = "decimal") -> Decimal:
    """Return a finite exact counter value, enforcing the configured type."""
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f'Counter value "{value}" is not numeric.') from error
    if not number.is_finite():
        raise ValueError("Counter values must be finite numbers.")
    kind = str(numeric_type).strip().casefold()
    if kind not in NUMERIC_TYPES:
        raise ValueError(f'Unknown counter numeric type: "{numeric_type}".')
    if kind == "integer" and number != number.to_integral_value():
        raise ValueError(f'Counter value "{value}" must be a whole number.')
    return number


def counter_number_to_storage(value: Any, numeric_type: str = "decimal") -> str:
    number = parse_counter_number(value, numeric_type)
    if number == 0:
        return "0"
    return format(number, "f")


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
    minimum: Decimal = Decimal("0")
    numeric_type: str = "integer"
    reset_value: Decimal = Decimal("0")
    display_precision: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "counter_id", validate_counter_id(self.counter_id))
        name = self.display_name.strip()
        if not name:
            raise ValueError("Counter name is required.")
        object.__setattr__(self, "display_name", name)
        object.__setattr__(self, "singular", self.singular.strip())
        object.__setattr__(self, "plural", self.plural.strip())
        numeric_type = str(self.numeric_type).strip().casefold()
        if numeric_type not in NUMERIC_TYPES:
            raise ValueError("Counter type must be Integer or Decimal.")
        minimum = parse_counter_number(self.minimum, numeric_type)
        reset_value = parse_counter_number(self.reset_value, numeric_type)
        precision = int(self.display_precision)
        if not 0 <= precision <= 6:
            raise ValueError("Display precision must be between 0 and 6.")
        if numeric_type == "integer" and precision != 0:
            raise ValueError("Integer counters use a display precision of 0.")
        object.__setattr__(self, "numeric_type", numeric_type)
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "reset_value", reset_value)
        object.__setattr__(self, "display_precision", precision)
        if not any(self.tracks(scope) for scope in SCOPES):
            raise ValueError("Select at least one tracked counter scope.")
        if not self.allow_negative and self.minimum < 0:
            raise ValueError("Minimum cannot be negative unless negative values are allowed.")
        if self.reset_value < self.minimum:
            raise ValueError("Reset value cannot be below the configured minimum.")

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "CounterDefinition":
        return cls(
            counter_id=str(values.get("counter_id", "")), display_name=str(values.get("display_name", "")),
            singular=str(values.get("singular", "")), plural=str(values.get("plural", "")),
            enabled=bool(values.get("enabled", True)), track_channel_total=bool(values.get("track_channel_total", True)),
            track_stream_total=bool(values.get("track_stream_total", True)), track_viewer_total=bool(values.get("track_viewer_total", True)),
            track_viewer_stream_total=bool(values.get("track_viewer_stream_total", False)), exclude_known_bots=bool(values.get("exclude_known_bots", True)),
            allow_negative=bool(values.get("allow_negative", False)),
            minimum=values.get("minimum", "0"),
            numeric_type=str(values.get("numeric_type", "integer")),
            reset_value=values.get("reset_value", "0"),
            display_precision=int(values.get("display_precision", 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        values = {name: getattr(self, name) for name in self.__dataclass_fields__}
        values["minimum"] = counter_number_to_storage(self.minimum, self.numeric_type)
        values["reset_value"] = counter_number_to_storage(self.reset_value, self.numeric_type)
        return values

    def updated(self, **changes: Any) -> "CounterDefinition":
        changes.pop("counter_id", None)
        return replace(self, **changes)

    def tracks(self, scope: str) -> bool:
        return bool(getattr(self, f"track_{scope}", False))


@dataclass(frozen=True, slots=True)
class CounterValues:
    channel_total: Decimal = Decimal("0")
    stream_total: Decimal = Decimal("0")
    viewer_total: Decimal = Decimal("0")
    viewer_stream_total: Decimal = Decimal("0")
    viewer_display_name: str = ""
    viewer_login: str = ""
