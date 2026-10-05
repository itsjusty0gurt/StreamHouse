from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from products.hub.automation.models import TriggerEvent
from products.hub.automation.routines import RoutineStore
from products.hub.integrations.music_player import (
    MUSIC_AUTOMATION_EVENT_TYPES,
    MusicPlayerEvent,
)
from shared.streamhouse_runtime.json_store import (
    UnsupportedJsonSchemaError,
    atomic_write_json,
    json_store_exists,
    load_validated_json,
)
from shared.streamhouse_runtime.paths import user_data_root


MUSIC_TRIGGER_TYPES = {
    "track.changed": "Track Changed",
    "playback.started": "Playback Started",
    "playback.paused": "Playback Paused",
    "playback.stopped": "Playback Stopped",
    "volume.changed": "Volume Changed",
    "player.connected": "Player Connected",
    "player.disconnected": "Player Disconnected",
}


@dataclass(slots=True)
class MusicAutomationTrigger:
    trigger_id: str
    routine_id: str
    event_type: str
    enabled: bool = True

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> MusicAutomationTrigger:
        return cls(
            trigger_id=str(values.get("trigger_id", "")),
            routine_id=str(values.get("routine_id", "")),
            event_type=str(values.get("event_type", "")).strip(),
            enabled=bool(values.get("enabled", True)),
        )


class MusicTriggerStore:
    """Persistent routine bindings for normalized MusicPlayerService events."""

    VERSION = 1

    def __init__(
        self,
        path: Path | None = None,
        routine_store: RoutineStore | None = None,
    ) -> None:
        self.path = path or user_data_root() / "automation" / "music_triggers.json"
        self.routine_store = routine_store or RoutineStore()
        self.triggers: list[MusicAutomationTrigger] = []

    def load(self) -> list[MusicAutomationTrigger]:
        if not self.routine_store.routines and self.routine_store.path.exists():
            self.routine_store.load()
        if not json_store_exists(self.path):
            self.triggers = []
            return []
        self.triggers = load_validated_json(self.path, self._parse_payload)
        return list(self.triggers)

    def _parse_payload(self, payload: object) -> list[MusicAutomationTrigger]:
        if not isinstance(payload, dict):
            raise ValueError("Music triggers must contain a JSON object.")
        version = payload.get("version")
        if type(version) is not int or version != self.VERSION:
            raise UnsupportedJsonSchemaError(
                f"Unsupported Music trigger version {version}; expected {self.VERSION}."
            )
        values = payload.get("triggers", [])
        if not isinstance(values, list):
            raise ValueError("Music triggers must contain a trigger list.")
        loaded: list[MusicAutomationTrigger] = []
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("Every Music trigger must be a JSON object.")
            trigger = MusicAutomationTrigger.from_dict(value)
            self._validate(trigger)
            routine = self.routine_store.get(trigger.routine_id)
            if routine is None or trigger.trigger_id not in routine.trigger_ids:
                raise ValueError("Music trigger has no linked routine.")
            loaded.append(trigger)
        return loaded

    def save(self) -> None:
        atomic_write_json(
            self.path,
            {"version": self.VERSION, "triggers": [asdict(item) for item in self.triggers]},
        )

    def add(self, routine_id: str, event_type: str, *, enabled: bool = True) -> MusicAutomationTrigger:
        trigger = MusicAutomationTrigger(uuid4().hex, routine_id, event_type.strip(), bool(enabled))
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

    def update(self, trigger_id: str, *, event_type: str, enabled: bool | None = None) -> MusicAutomationTrigger:
        trigger = self.get(trigger_id)
        if trigger is None:
            raise ValueError("The selected Music trigger no longer exists.")
        candidate = MusicAutomationTrigger(
            trigger.trigger_id,
            trigger.routine_id,
            event_type.strip(),
            trigger.enabled if enabled is None else bool(enabled),
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

    def get(self, trigger_id: str) -> MusicAutomationTrigger | None:
        return next((item for item in self.triggers if item.trigger_id == trigger_id), None)

    def for_routine(self, routine_id: str) -> tuple[MusicAutomationTrigger, ...]:
        return tuple(item for item in self.triggers if item.routine_id == routine_id)

    def evaluate(self, event: MusicPlayerEvent) -> tuple[TriggerEvent, ...]:
        if event.event_type not in MUSIC_AUTOMATION_EVENT_TYPES:
            return ()
        context = {"event": MUSIC_TRIGGER_TYPES[event.event_type], "event_type": event.event_type, **event.context}
        return tuple(
            TriggerEvent(
                trigger_id=item.trigger_id,
                service="music",
                trigger_type=event.event_type,
                context=context,
            )
            for item in self.triggers
            if item.enabled and item.event_type == event.event_type
        )

    @staticmethod
    def _validate(trigger: MusicAutomationTrigger) -> None:
        if not trigger.trigger_id or not trigger.routine_id:
            raise ValueError("Music triggers require IDs.")
        if trigger.event_type not in MUSIC_TRIGGER_TYPES:
            raise ValueError("That Music trigger is not supported.")
