from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from automation.models import TriggerEvent
from automation.routines import RoutineStore
from core.json_store import atomic_write_json, load_json_with_backup
from core.paths import user_data_root
from obs_service.models import ObsEvent


OBS_TRIGGER_TYPES = {
    "ConnectionOpened": "OBS Connected",
    "ConnectionClosed": "OBS Disconnected",
    "CurrentProgramSceneChanged": "Scene Changed",
    "CurrentPreviewSceneChanged": "Preview Scene Changed",
    "StreamStateChanged": "Streaming State Changed",
    "RecordStateChanged": "Recording State Changed",
    "ReplayBufferStateChanged": "Replay Buffer State Changed",
    "SceneItemEnableStateChanged": "Source Visibility Changed",
    "InputMuteStateChanged": "Input Mute Changed",
    "InputVolumeChanged": "Input Volume Changed",
    "MediaInputPlaybackStarted": "Media Playback Started",
    "MediaInputPlaybackEnded": "Media Playback Ended",
    "StudioModeStateChanged": "Studio Mode Changed",
    "ExitStarted": "OBS Exit Started",
}

OBS_TRIGGER_SLUGS = {
    "ConnectionOpened": "connected",
    "ConnectionClosed": "disconnected",
    "CurrentProgramSceneChanged": "scene.program_changed",
    "CurrentPreviewSceneChanged": "scene.preview_changed",
    "StreamStateChanged": "stream.state_changed",
    "RecordStateChanged": "record.state_changed",
    "ReplayBufferStateChanged": "replay_buffer.state_changed",
    "SceneItemEnableStateChanged": "source.visibility_changed",
    "InputMuteStateChanged": "input.mute_changed",
    "InputVolumeChanged": "input.volume_changed",
    "MediaInputPlaybackStarted": "media.started",
    "MediaInputPlaybackEnded": "media.ended",
    "StudioModeStateChanged": "studio_mode.changed",
    "ExitStarted": "exit.started",
}


@dataclass(slots=True)
class ObsAutomationTrigger:
    trigger_id: str
    routine_id: str
    event_type: str
    filters: dict[str, str] = field(default_factory=dict)
    enabled: bool = True

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> ObsAutomationTrigger:
        raw_filters = values.get("filters", {})
        return cls(
            trigger_id=str(values.get("trigger_id", "")) or uuid4().hex,
            routine_id=str(values.get("routine_id", "")),
            event_type=str(values.get("event_type", "")).strip(),
            filters={
                str(key).strip(): str(value).strip()
                for key, value in raw_filters.items()
                if str(key).strip() and str(value).strip()
            } if isinstance(raw_filters, dict) else {},
            enabled=bool(values.get("enabled", True)),
        )


class ObsTriggerStore:
    VERSION = 1

    def __init__(
        self,
        path: Path | None = None,
        routine_store: RoutineStore | None = None,
    ) -> None:
        self.path = path or user_data_root() / "obs" / "triggers.json"
        self.routine_store = routine_store or RoutineStore()
        self.triggers: list[ObsAutomationTrigger] = []

    def load(self) -> list[ObsAutomationTrigger]:
        if not self.routine_store.routines and self.routine_store.path.exists():
            self.routine_store.load()
        if not self.path.exists():
            self.triggers = []
            return []
        payload = load_json_with_backup(self.path)
        if not isinstance(payload, dict):
            raise ValueError("OBS triggers must contain a JSON object.")
        values = payload.get("triggers", [])
        if not isinstance(values, list):
            raise ValueError("OBS triggers must contain a trigger list.")
        loaded: list[ObsAutomationTrigger] = []
        for value in values:
            if not isinstance(value, dict):
                continue
            try:
                trigger = ObsAutomationTrigger.from_dict(value)
                self._validate(trigger)
                routine = self.routine_store.get(trigger.routine_id)
                if routine is None or trigger.trigger_id not in routine.trigger_ids:
                    raise ValueError("OBS trigger has no linked routine.")
            except (TypeError, ValueError):
                continue
            loaded.append(trigger)
        self.triggers = loaded
        return list(loaded)

    def save(self) -> None:
        atomic_write_json(
            self.path,
            {"version": self.VERSION, "triggers": [asdict(item) for item in self.triggers]},
        )

    def add(
        self,
        routine_id: str,
        event_type: str,
        *,
        filters: Mapping[str, str] | None = None,
        enabled: bool = True,
    ) -> ObsAutomationTrigger:
        trigger = ObsAutomationTrigger(
            uuid4().hex,
            routine_id,
            event_type.strip(),
            self._clean_filters(filters or {}),
            bool(enabled),
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
        filters: Mapping[str, str] | None = None,
        enabled: bool | None = None,
    ) -> ObsAutomationTrigger:
        trigger = self.get(trigger_id)
        if trigger is None:
            raise ValueError("The selected OBS trigger no longer exists.")
        candidate = ObsAutomationTrigger(
            trigger.trigger_id,
            trigger.routine_id,
            event_type.strip(),
            self._clean_filters(filters or {}),
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

    def get(self, trigger_id: str) -> ObsAutomationTrigger | None:
        return next((item for item in self.triggers if item.trigger_id == trigger_id), None)

    def for_routine(self, routine_id: str) -> tuple[ObsAutomationTrigger, ...]:
        return tuple(item for item in self.triggers if item.routine_id == routine_id)

    def evaluate(self, event: ObsEvent) -> tuple[TriggerEvent, ...]:
        context = self.context_for(event)
        return tuple(
            TriggerEvent(
                trigger_id=item.trigger_id,
                service="obs",
                trigger_type=OBS_TRIGGER_SLUGS[event.event_type],
                context=context,
            )
            for item in self.triggers
            if item.enabled
            and item.event_type == event.event_type
            and self._matches(event.event_data, item.filters)
        )

    @staticmethod
    def context_for(event: ObsEvent) -> dict[str, str]:
        values = event.event_data
        def first(*keys: str) -> str:
            for key in keys:
                value = values.get(key)
                if value is not None and str(value).strip():
                    return str(value)
            return "--"

        muted_value = values.get("inputMuted")
        if isinstance(muted_value, bool):
            muted = "Muted" if muted_value else "Not Muted"
        elif str(muted_value).strip().casefold() in {"true", "1", "yes", "on"}:
            muted = "Muted"
        elif str(muted_value).strip().casefold() in {"false", "0", "no", "off"}:
            muted = "Not Muted"
        else:
            muted = "--" if muted_value is None else str(muted_value).strip()
        return {
            "event": OBS_TRIGGER_TYPES.get(event.event_type, event.event_type),
            "event_type": event.event_type,
            "scene": first("sceneName", "scene-name"),
            "source": first("sourceName", "sceneItemName", "inputName"),
            "input": first("inputName"),
            "output_state": first("outputState"),
            "enabled": first("sceneItemEnabled", "studioModeEnabled"),
            "mute": muted,
            "muted": muted,
            "volume_db": first("inputVolumeDb"),
            "media": first("inputName"),
            "channel": "--", "user": "--", "message": "--", "amount": "--",
            "bits": "--", "viewers": "--", "tier": "--", "reward": "--",
            "reward_id": "--", "reward_cost": "--", "title": "--", "game": "--",
            "uptime": "--", "followers": "--", "command": "--", "args": "--",
            "target": "--", "uses": "--",
        }

    @staticmethod
    def _matches(values: Mapping[str, Any], filters: Mapping[str, str]) -> bool:
        return all(str(values.get(key, "")).casefold() == expected.casefold() for key, expected in filters.items())

    @staticmethod
    def _clean_filters(filters: Mapping[str, str]) -> dict[str, str]:
        return {str(key).strip(): str(value).strip() for key, value in filters.items() if str(key).strip() and str(value).strip()}

    @staticmethod
    def _validate(trigger: ObsAutomationTrigger) -> None:
        if not trigger.trigger_id or not trigger.routine_id:
            raise ValueError("OBS triggers require IDs.")
        if trigger.event_type not in OBS_TRIGGER_TYPES:
            raise ValueError("That OBS trigger is not supported.")
