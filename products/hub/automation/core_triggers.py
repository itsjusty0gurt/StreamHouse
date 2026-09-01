from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from math import isfinite
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from products.hub.automation.models import TriggerEvent
from products.hub.automation.routines import RoutineStore
from shared.streamhouse_runtime.json_store import atomic_write_json, load_json_with_backup
from shared.streamhouse_runtime.paths import user_data_root


CORE_TRIGGER_TYPES = {
    "application.started": "Application Started",
    "application.closing": "Application Closing",
    "timer": "Timer",
}
TIMER_MODES = {"fixed": "Fixed Interval", "random": "Random Range"}
TIMER_UNITS = {"seconds": Decimal("1"), "minutes": Decimal("60"), "hours": Decimal("3600")}


@dataclass(slots=True)
class CoreAutomationTrigger:
    trigger_id: str
    routine_id: str
    event_type: str
    enabled: bool = True
    timer_mode: str = ""
    timer_minimum: str = ""
    timer_minimum_unit: str = "seconds"
    timer_maximum: str = ""
    timer_maximum_unit: str = "seconds"

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> CoreAutomationTrigger:
        return cls(
            trigger_id=str(values.get("trigger_id", "")),
            routine_id=str(values.get("routine_id", "")),
            event_type=str(values.get("event_type", "")).strip(),
            enabled=bool(values.get("enabled", True)),
            timer_mode=str(values.get("timer_mode", "")).strip(),
            timer_minimum=str(values.get("timer_minimum", "")).strip(),
            timer_minimum_unit=str(values.get("timer_minimum_unit", "seconds")).strip(),
            timer_maximum=str(values.get("timer_maximum", "")).strip(),
            timer_maximum_unit=str(values.get("timer_maximum_unit", "seconds")).strip(),
        )


class CoreTriggerStore:
    """Persistent bindings between Streamhouse Hub lifecycle events and routines."""

    VERSION = 2

    def __init__(
        self,
        path: Path | None = None,
        routine_store: RoutineStore | None = None,
    ) -> None:
        self.path = path or user_data_root() / "automation" / "core_triggers.json"
        self.routine_store = routine_store or RoutineStore()
        self.triggers: list[CoreAutomationTrigger] = []
        self._change_subscribers: list[Callable[[], None]] = []

    def subscribe_changes(self, callback: Callable[[], None]) -> None:
        if callback not in self._change_subscribers:
            self._change_subscribers.append(callback)

    def unsubscribe_changes(self, callback: Callable[[], None]) -> None:
        if callback in self._change_subscribers:
            self._change_subscribers.remove(callback)

    def _notify_changed(self) -> None:
        for callback in tuple(self._change_subscribers):
            callback()

    def load(self) -> list[CoreAutomationTrigger]:
        if not self.routine_store.routines and self.routine_store.path.exists():
            self.routine_store.load()
        if not self.path.exists():
            self.triggers = []
            return []
        payload = load_json_with_backup(self.path)
        if not isinstance(payload, dict):
            raise ValueError("Core triggers must contain a JSON object.")
        version = payload.get("version")
        if type(version) is not int or version != self.VERSION:
            raise ValueError(
                f"Unsupported Core trigger version {version}; expected {self.VERSION}."
            )
        values = payload.get("triggers", [])
        if not isinstance(values, list):
            raise ValueError("Core triggers must contain a trigger list.")
        loaded: list[CoreAutomationTrigger] = []
        for value in values:
            if not isinstance(value, dict):
                continue
            try:
                trigger = CoreAutomationTrigger.from_dict(value)
                self._validate(trigger)
                routine = self.routine_store.get(trigger.routine_id)
                if routine is None or trigger.trigger_id not in routine.trigger_ids:
                    raise ValueError("Core trigger has no linked routine.")
            except (TypeError, ValueError):
                continue
            loaded.append(trigger)
        self.triggers = loaded
        return list(loaded)

    def save(self) -> None:
        atomic_write_json(
            self.path,
            {
                "version": self.VERSION,
                "triggers": [asdict(trigger) for trigger in self.triggers],
            },
        )

    def add(
        self,
        routine_id: str,
        event_type: str,
        *,
        enabled: bool = True,
        timer_mode: str = "",
        timer_minimum: str = "",
        timer_minimum_unit: str = "seconds",
        timer_maximum: str = "",
        timer_maximum_unit: str = "seconds",
    ) -> CoreAutomationTrigger:
        trigger = CoreAutomationTrigger(
            trigger_id=uuid4().hex,
            routine_id=routine_id,
            event_type=event_type.strip(),
            enabled=bool(enabled),
            timer_mode=timer_mode.strip(),
            timer_minimum=timer_minimum.strip(),
            timer_minimum_unit=timer_minimum_unit.strip(),
            timer_maximum=timer_maximum.strip(),
            timer_maximum_unit=timer_maximum_unit.strip(),
        )
        self._validate(trigger)
        if self.routine_store.get(routine_id) is None:
            raise ValueError("The selected routine no longer exists.")
        self.routine_store.link_trigger(routine_id, trigger.trigger_id)
        self.triggers.append(trigger)
        try:
            self.save()
        except OSError:
            self.triggers.remove(trigger)
            self.routine_store.unlink_trigger(routine_id, trigger.trigger_id)
            raise
        self._notify_changed()
        return trigger

    def update(
        self,
        trigger_id: str,
        *,
        event_type: str,
        enabled: bool | None = None,
        timer_mode: str = "",
        timer_minimum: str = "",
        timer_minimum_unit: str = "seconds",
        timer_maximum: str = "",
        timer_maximum_unit: str = "seconds",
    ) -> CoreAutomationTrigger:
        trigger = self.get(trigger_id)
        if trigger is None:
            raise ValueError("The selected Core trigger no longer exists.")
        candidate = CoreAutomationTrigger(
            trigger_id=trigger.trigger_id,
            routine_id=trigger.routine_id,
            event_type=event_type.strip(),
            enabled=trigger.enabled if enabled is None else bool(enabled),
            timer_mode=timer_mode.strip(),
            timer_minimum=timer_minimum.strip(),
            timer_minimum_unit=timer_minimum_unit.strip(),
            timer_maximum=timer_maximum.strip(),
            timer_maximum_unit=timer_maximum_unit.strip(),
        )
        self._validate(candidate)
        index = self.triggers.index(trigger)
        self.triggers[index] = candidate
        try:
            self.save()
        except OSError:
            self.triggers[index] = trigger
            raise
        self._notify_changed()
        return candidate

    def delete(self, trigger_id: str) -> bool:
        trigger = self.get(trigger_id)
        if trigger is None:
            return False
        self.routine_store.unlink_trigger(trigger.routine_id, trigger.trigger_id)
        self.triggers.remove(trigger)
        try:
            self.save()
        except OSError:
            self.triggers.append(trigger)
            self.routine_store.link_trigger(trigger.routine_id, trigger.trigger_id)
            raise
        self._notify_changed()
        return True

    def add_timer(
        self,
        routine_id: str,
        *,
        timer_mode: str,
        timer_minimum: str,
        timer_minimum_unit: str,
        timer_maximum: str = "",
        timer_maximum_unit: str = "seconds",
        enabled: bool = True,
    ) -> CoreAutomationTrigger:
        return self.add(
            routine_id,
            "timer",
            enabled=enabled,
            timer_mode=timer_mode,
            timer_minimum=timer_minimum,
            timer_minimum_unit=timer_minimum_unit,
            timer_maximum=timer_maximum,
            timer_maximum_unit=timer_maximum_unit,
        )

    def update_timer(
        self,
        trigger_id: str,
        *,
        timer_mode: str,
        timer_minimum: str,
        timer_minimum_unit: str,
        timer_maximum: str = "",
        timer_maximum_unit: str = "seconds",
        enabled: bool | None = None,
    ) -> CoreAutomationTrigger:
        return self.update(
            trigger_id,
            event_type="timer",
            enabled=enabled,
            timer_mode=timer_mode,
            timer_minimum=timer_minimum,
            timer_minimum_unit=timer_minimum_unit,
            timer_maximum=timer_maximum,
            timer_maximum_unit=timer_maximum_unit,
        )

    def get(self, trigger_id: str) -> CoreAutomationTrigger | None:
        return next(
            (
                trigger
                for trigger in self.triggers
                if trigger.trigger_id == trigger_id
            ),
            None,
        )

    def for_routine(self, routine_id: str) -> tuple[CoreAutomationTrigger, ...]:
        return tuple(
            trigger for trigger in self.triggers if trigger.routine_id == routine_id
        )

    def evaluate(
        self,
        event_type: str,
        context: Mapping[str, str] | None = None,
    ) -> tuple[TriggerEvent, ...]:
        clean_type = event_type.strip()
        values = {
            "event": CORE_TRIGGER_TYPES.get(clean_type, clean_type),
            "event_type": clean_type,
            **dict(context or {}),
        }
        return tuple(
            TriggerEvent(
                trigger_id=trigger.trigger_id,
                service="core",
                trigger_type=clean_type,
                context=values,
            )
            for trigger in self.triggers
            if trigger.enabled and trigger.event_type == clean_type
        )

    def event_for(self, trigger_id: str) -> TriggerEvent | None:
        trigger = self.get(trigger_id)
        if trigger is None or not trigger.enabled or trigger.event_type != "timer":
            return None
        return TriggerEvent(
            trigger_id=trigger.trigger_id,
            service="core",
            trigger_type="timer",
            context={"event": "Timer", "event_type": "timer"},
        )

    @classmethod
    def timer_bounds_seconds(cls, trigger: CoreAutomationTrigger) -> tuple[float, float]:
        minimum = cls._seconds(trigger.timer_minimum, trigger.timer_minimum_unit)
        maximum = (
            minimum
            if trigger.timer_mode == "fixed"
            else cls._seconds(trigger.timer_maximum, trigger.timer_maximum_unit)
        )
        values = (float(minimum), float(maximum))
        if not all(isfinite(value) for value in values):
            raise ValueError("Timer duration is too large.")
        return values

    @staticmethod
    def timer_description(trigger: CoreAutomationTrigger) -> str:
        minimum_unit = CoreTriggerStore._display_unit(
            trigger.timer_minimum,
            trigger.timer_minimum_unit,
        )
        if trigger.timer_mode == "random":
            maximum_unit = CoreTriggerStore._display_unit(
                trigger.timer_maximum,
                trigger.timer_maximum_unit,
            )
            if trigger.timer_minimum_unit == trigger.timer_maximum_unit:
                return (
                    f"Random {trigger.timer_minimum}–{trigger.timer_maximum} "
                    f"{maximum_unit}"
                )
            return (
                f"Random {trigger.timer_minimum} {minimum_unit}–"
                f"{trigger.timer_maximum} {maximum_unit}"
            )
        return f"Fixed {trigger.timer_minimum} {minimum_unit}"

    @staticmethod
    def _display_unit(value: str, unit: str) -> str:
        try:
            singular = Decimal(value) == 1
        except InvalidOperation:
            singular = False
        return unit[:-1] if singular else unit

    @staticmethod
    def _seconds(value: str, unit: str) -> Decimal:
        if unit not in TIMER_UNITS:
            raise ValueError("Timer unit must be Seconds, Minutes, or Hours.")
        try:
            number = Decimal(value)
        except (InvalidOperation, ValueError):
            raise ValueError("Timer values must be positive numbers.") from None
        if not number.is_finite() or number <= 0:
            raise ValueError("Timer values must be positive finite numbers.")
        return number * TIMER_UNITS[unit]

    @staticmethod
    def _validate(trigger: CoreAutomationTrigger) -> None:
        if not trigger.trigger_id or not trigger.routine_id:
            raise ValueError("Core triggers require IDs.")
        if trigger.event_type not in CORE_TRIGGER_TYPES:
            raise ValueError("That Core program trigger is not supported.")
        if trigger.event_type == "timer":
            if trigger.timer_mode not in TIMER_MODES:
                raise ValueError("Choose Fixed Interval or Random Range.")
            minimum, maximum = CoreTriggerStore.timer_bounds_seconds(trigger)
            if minimum > maximum:
                raise ValueError("Random timer minimum must not exceed its maximum.")
        elif any((trigger.timer_mode, trigger.timer_minimum, trigger.timer_maximum)):
            raise ValueError("Only Timer triggers may contain timer settings.")
