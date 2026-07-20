from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from automation.models import TriggerEvent
from automation.routines import RoutineStore
from core.json_store import atomic_write_json, load_json_with_backup
from core.paths import user_data_root
from twitch.catalog import EVENTSUB_SUBSCRIPTIONS
from twitch.models import TwitchEvent


TWITCH_EVENT_TYPES = tuple(
    sorted({subscription.type for subscription in EVENTSUB_SUBSCRIPTIONS})
)
TWITCH_AUTOMATION_EVENT_TYPES = (
    "channel.follow",
    "channel.subscribe",
    "channel.subscription.gift",
    "channel.subscription.message",
    "channel.cheer",
    "channel.raid",
    "channel.channel_points_custom_reward_redemption.add",
    "stream.online",
    "stream.offline",
)


@dataclass(slots=True)
class TwitchEventAutomationTrigger:
    trigger_id: str
    routine_id: str
    event_type: str
    filters: dict[str, str] = field(default_factory=dict)
    enabled: bool = True

    @classmethod
    def from_dict(
        cls, values: Mapping[str, Any]
    ) -> TwitchEventAutomationTrigger:
        raw_filters = values.get("filters", {})
        return cls(
            trigger_id=str(values.get("trigger_id", "")) or uuid4().hex,
            routine_id=str(values.get("routine_id", "")),
            event_type=str(values.get("event_type", "")).strip(),
            filters={
                str(key).strip(): str(value).strip()
                for key, value in raw_filters.items()
                if str(key).strip() and str(value).strip()
            }
            if isinstance(raw_filters, dict)
            else {},
            enabled=bool(values.get("enabled", True)),
        )


class TwitchEventTriggerStore:
    VERSION = 1

    def __init__(
        self,
        path: Path | None = None,
        routine_store: RoutineStore | None = None,
    ) -> None:
        self.path = path or user_data_root() / "twitch" / "event_triggers.json"
        self.routine_store = routine_store or RoutineStore()
        self.triggers: list[TwitchEventAutomationTrigger] = []

    def load(self) -> list[TwitchEventAutomationTrigger]:
        if not self.routine_store.routines and self.routine_store.path.exists():
            self.routine_store.load()
        if not self.path.exists():
            self.triggers = []
            return []
        payload = load_json_with_backup(self.path)
        if not isinstance(payload, dict):
            raise ValueError("Twitch event triggers must contain a JSON object.")
        if int(payload.get("version", 1)) > self.VERSION:
            raise ValueError("Twitch event trigger data is newer than this app.")
        values = payload.get("triggers", [])
        if not isinstance(values, list):
            raise ValueError("Twitch event triggers must contain a trigger list.")
        loaded: list[TwitchEventAutomationTrigger] = []
        for value in values:
            if not isinstance(value, dict):
                continue
            try:
                trigger = TwitchEventAutomationTrigger.from_dict(value)
                self._validate(trigger)
                routine = self.routine_store.get(trigger.routine_id)
                if routine is None or trigger.trigger_id not in routine.trigger_ids:
                    raise ValueError("Twitch event trigger has no linked routine.")
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
        filters: Mapping[str, str] | None = None,
        enabled: bool = True,
    ) -> TwitchEventAutomationTrigger:
        trigger = TwitchEventAutomationTrigger(
            trigger_id=uuid4().hex,
            routine_id=routine_id,
            event_type=event_type.strip(),
            filters=self._clean_filters(filters or {}),
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
        filters: Mapping[str, str] | None = None,
        enabled: bool | None = None,
    ) -> TwitchEventAutomationTrigger:
        trigger = self.get(trigger_id)
        if trigger is None:
            raise ValueError("The selected Twitch event trigger no longer exists.")
        candidate = TwitchEventAutomationTrigger(
            trigger_id=trigger.trigger_id,
            routine_id=trigger.routine_id,
            event_type=event_type.strip(),
            filters=self._clean_filters(filters or {}),
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

    def get(self, trigger_id: str) -> TwitchEventAutomationTrigger | None:
        return next(
            (
                trigger
                for trigger in self.triggers
                if trigger.trigger_id == trigger_id
            ),
            None,
        )

    def for_routine(
        self, routine_id: str
    ) -> tuple[TwitchEventAutomationTrigger, ...]:
        return tuple(
            trigger for trigger in self.triggers if trigger.routine_id == routine_id
        )

    def evaluate(self, twitch_event: TwitchEvent) -> tuple[TriggerEvent, ...]:
        event = twitch_event.payload.get("event", {})
        if not isinstance(event, dict):
            event = {}
        context = self.context_for(twitch_event, event)
        return tuple(
            TriggerEvent(
                trigger_id=trigger.trigger_id,
                service="twitch",
                trigger_type="eventsub",
                context=context,
            )
            for trigger in self.triggers
            if trigger.enabled
            and trigger.event_type == twitch_event.subscription_type
            and self._matches(event, trigger.filters)
        )

    @classmethod
    def context_for(
        cls,
        twitch_event: TwitchEvent,
        event: Mapping[str, Any],
    ) -> dict[str, str]:
        reward = event.get("reward", {})
        if not isinstance(reward, dict):
            reward = {}
        user = cls._first(
            event,
            "user_name",
            "from_broadcaster_user_name",
            "moderator_user_name",
            "chatter_user_name",
        )
        channel = cls._first(
            event,
            "broadcaster_user_name",
            "to_broadcaster_user_name",
        ) or twitch_event.broadcaster_user_name
        user_input = cls._first(event, "user_input")
        message = cls._first(event, "message", "text") or user_input
        amount = cls._first(event, "bits", "total", "viewers", "amount")
        if not amount:
            amount = str(reward.get("cost", ""))
        return {
            "user": user or "--",
            "channel": channel or twitch_event.broadcaster_user_login or "--",
            "event": twitch_event.subscription_type.rsplit(".", 1)[-1],
            "event_type": twitch_event.subscription_type,
            "message": message or "--",
            "input": user_input or "--",
            "amount": amount or "--",
            "bits": cls._first(event, "bits") or "--",
            "viewers": cls._first(event, "viewers") or "--",
            "tier": cls._first(event, "tier") or "--",
            "reward": str(reward.get("title", "")) or "--",
            "reward_id": str(reward.get("id", "")) or "--",
            "reward_cost": str(reward.get("cost", "")) or "--",
            "user_id": cls._first(
                event,
                "user_id",
                "chatter_user_id",
                "from_broadcaster_user_id",
                "moderator_user_id",
            ) or "--",
            "target_user_id": cls._first(event, "target_user_id") or "--",
            "message_id": cls._first(event, "message_id") or "--",
            "redemption_id": (
                cls._first(event, "id")
                if "redemption" in twitch_event.subscription_type
                else ""
            ) or "--",
            "title": cls._first(event, "title") or "--",
            "game": cls._first(event, "category_name") or "--",
            "uptime": "--",
            "followers": "--",
            "command": "--",
            "args": "--",
            "target": "--",
            "uses": "--",
        }

    @staticmethod
    def _first(values: Mapping[str, Any], *keys: str) -> str:
        for key in keys:
            value = values.get(key)
            if value is not None and str(value).strip():
                return str(value)
        return ""

    @classmethod
    def _matches(cls, event: Mapping[str, Any], filters: Mapping[str, str]) -> bool:
        return all(
            cls._nested_value(event, path).casefold() == expected.casefold()
            for path, expected in filters.items()
        )

    @staticmethod
    def _nested_value(values: Mapping[str, Any], path: str) -> str:
        current: Any = values
        for segment in path.split("."):
            if not isinstance(current, Mapping) or segment not in current:
                return ""
            current = current[segment]
        return str(current)

    @staticmethod
    def _clean_filters(filters: Mapping[str, str]) -> dict[str, str]:
        return {
            str(key).strip(): str(value).strip()
            for key, value in filters.items()
            if str(key).strip() and str(value).strip()
        }

    @staticmethod
    def _validate(trigger: TwitchEventAutomationTrigger) -> None:
        if not trigger.trigger_id or not trigger.routine_id:
            raise ValueError("Twitch event triggers require IDs.")
        if trigger.event_type not in TWITCH_AUTOMATION_EVENT_TYPES:
            raise ValueError(
                "That Twitch EventSub type is not connected for live automation yet."
            )
        for path in trigger.filters:
            if any(not segment.strip() for segment in path.split(".")):
                raise ValueError("Twitch event filter paths cannot be empty.")
