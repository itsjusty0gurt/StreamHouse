from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from automation.models import TriggerEvent
from automation.routines import RoutineStore
from core.json_store import atomic_write_json, load_json_with_backup
from core.paths import user_data_root


CORE_TRIGGER_TYPES = {
    "application.started": "Application Started",
    "application.closing": "Application Closing",
}


@dataclass(slots=True)
class CoreAutomationTrigger:
    trigger_id: str
    routine_id: str
    event_type: str
    enabled: bool = True

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> CoreAutomationTrigger:
        return cls(
            trigger_id=str(values.get("trigger_id", "")) or uuid4().hex,
            routine_id=str(values.get("routine_id", "")),
            event_type=str(values.get("event_type", "")).strip(),
            enabled=bool(values.get("enabled", True)),
        )


class CoreTriggerStore:
    """Persistent bindings between Sally lifecycle events and routines."""

    VERSION = 1

    def __init__(
        self,
        path: Path | None = None,
        routine_store: RoutineStore | None = None,
    ) -> None:
        self.path = path or user_data_root() / "automation" / "core_triggers.json"
        self.routine_store = routine_store or RoutineStore()
        self.triggers: list[CoreAutomationTrigger] = []

    def load(self) -> list[CoreAutomationTrigger]:
        if not self.routine_store.routines and self.routine_store.path.exists():
            self.routine_store.load()
        if not self.path.exists():
            self.triggers = []
            return []
        payload = load_json_with_backup(self.path)
        if not isinstance(payload, dict):
            raise ValueError("Core triggers must contain a JSON object.")
        if int(payload.get("version", 1)) > self.VERSION:
            raise ValueError("Core trigger data is newer than this app.")
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
    ) -> CoreAutomationTrigger:
        trigger = CoreAutomationTrigger(
            trigger_id=uuid4().hex,
            routine_id=routine_id,
            event_type=event_type.strip(),
            enabled=bool(enabled),
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
        return trigger

    def update(
        self,
        trigger_id: str,
        *,
        event_type: str,
        enabled: bool | None = None,
    ) -> CoreAutomationTrigger:
        trigger = self.get(trigger_id)
        if trigger is None:
            raise ValueError("The selected Core trigger no longer exists.")
        candidate = CoreAutomationTrigger(
            trigger_id=trigger.trigger_id,
            routine_id=trigger.routine_id,
            event_type=event_type.strip(),
            enabled=trigger.enabled if enabled is None else bool(enabled),
        )
        self._validate(candidate)
        index = self.triggers.index(trigger)
        self.triggers[index] = candidate
        try:
            self.save()
        except OSError:
            self.triggers[index] = trigger
            raise
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
        return True

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

    @staticmethod
    def _validate(trigger: CoreAutomationTrigger) -> None:
        if not trigger.trigger_id or not trigger.routine_id:
            raise ValueError("Core triggers require IDs.")
        if trigger.event_type not in CORE_TRIGGER_TYPES:
            raise ValueError("That Core program trigger is not supported.")
