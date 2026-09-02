from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from time import monotonic
from typing import Any, Mapping
from uuid import uuid4

from products.hub.automation.models import TriggerEvent
from products.hub.automation.routines import RoutineStore
from shared.streamhouse_runtime.json_store import atomic_write_json, load_json_with_backup
from shared.streamhouse_runtime.logger import Logger
from shared.streamhouse_runtime.paths import user_data_root
from products.hub.twitch.catalog import EVENTSUB_SUBSCRIPTIONS
from products.hub.twitch.models import TwitchEvent, TwitchMessage


TWITCH_EVENT_TYPES = tuple(
    sorted({subscription.type for subscription in EVENTSUB_SUBSCRIPTIONS})
)
TWITCH_EVENT_AUTOMATION_TYPES = (
    "channel.follow",
    "channel.subscribe",
    "channel.subscription.gift",
    "channel.subscription.message",
    "channel.cheer",
    "channel.raid",
    "channel.raid.outgoing",
    "channel.channel_points_custom_reward_redemption.add",
    "stream.online",
    "stream.offline",
    "channel.chat.first_message",
)
CHANNEL_POINT_REDEMPTION_EVENT_TYPE = (
    "channel.channel_points_custom_reward_redemption.add"
)
KEYWORD_PHRASE_EVENT_TYPE = "channel.chat.keyword_phrase"
ADS_TRIGGER_TYPES = {
    "ads.warning.5_minutes": "5 Minute Warning",
    "ads.warning.3_minutes": "3 Minute Warning",
    "ads.warning.2_minutes": "2 Minute Warning",
    "ads.warning.1_minute": "1 Minute Warning",
    "ads.started": "Ads Started",
    "ads.ended": "Ads Ended",
}
TWITCH_AUTOMATION_EVENT_TYPES = (
    *TWITCH_EVENT_AUTOMATION_TYPES,
    KEYWORD_PHRASE_EVENT_TYPE,
    *ADS_TRIGGER_TYPES,
)
KEYWORD_MATCH_TYPES = {
    "contains": "Contains",
    "exact": "Exact Message",
    "starts_with": "Starts With",
    "ends_with": "Ends With",
}

SUBSCRIPTION_AUTOMATION_TYPES = {
    "channel.subscribe",
    "channel.subscription.message",
}


@dataclass(frozen=True, slots=True)
class _CorrelationEntry:
    event: TwitchEvent
    observed_at: float


class TwitchSubscriptionEventCorrelator:
    """Enrich one authoritative subscription event without firing twice."""

    def __init__(
        self,
        *,
        wait_seconds: float = 1.0,
        ttl_seconds: float = 10.0,
        max_entries: int = 256,
    ) -> None:
        self.wait_seconds = max(float(wait_seconds), 0.0)
        self.ttl_seconds = max(float(ttl_seconds), self.wait_seconds)
        self.max_entries = max(int(max_entries), 1)
        self._notifications: dict[tuple[str, str, str], deque[_CorrelationEntry]] = {}
        self._pending: dict[tuple[str, str, str], deque[_CorrelationEntry]] = {}
        self._recent_direct: dict[tuple[str, str, str], float] = {}

    def observe(
        self,
        event: TwitchEvent,
        *,
        now: float | None = None,
    ) -> tuple[TwitchEvent, ...]:
        observed_at = monotonic() if now is None else float(now)
        self._prune(observed_at)
        if event.subscription_type == "channel.chat.notification":
            target_type = self._notification_target(event)
            if not target_type:
                return (event,)
            key = self._key(event, target_type, chat_notification=True)
            pending = self._pop(self._pending, key)
            if pending is not None:
                self._remember_direct(key, observed_at)
                return (self._enrich(pending.event, event),)
            if key in self._recent_direct:
                return (event,)
            self._append(
                self._notifications,
                key,
                _CorrelationEntry(event, observed_at),
            )
            return (event,)
        if event.subscription_type not in SUBSCRIPTION_AUTOMATION_TYPES:
            return (event,)
        key = self._key(event, event.subscription_type)
        notification = self._pop(self._notifications, key)
        if notification is not None:
            self._remember_direct(key, observed_at)
            return (self._enrich(event, notification.event),)
        self._append(self._pending, key, _CorrelationEntry(event, observed_at))
        return ()

    def flush(
        self,
        *,
        now: float | None = None,
        force: bool = False,
    ) -> tuple[TwitchEvent, ...]:
        observed_at = monotonic() if now is None else float(now)
        ready: list[TwitchEvent] = []
        for key, entries in list(self._pending.items()):
            while entries and (
                force
                or observed_at - entries[0].observed_at >= self.wait_seconds
            ):
                entry = entries.popleft()
                ready.append(entry.event)
                self._remember_direct(key, observed_at)
            if not entries:
                self._pending.pop(key, None)
        self._prune(observed_at)
        return tuple(ready)

    def clear(self) -> None:
        self._notifications.clear()
        self._pending.clear()
        self._recent_direct.clear()

    @property
    def pending_count(self) -> int:
        return sum(len(entries) for entries in self._pending.values())

    @property
    def notification_count(self) -> int:
        return sum(len(entries) for entries in self._notifications.values())

    def _prune(self, now: float) -> None:
        for values in (self._notifications, self._pending):
            for key, entries in list(values.items()):
                while entries and now - entries[0].observed_at > self.ttl_seconds:
                    entries.popleft()
                if not entries:
                    values.pop(key, None)
        self._recent_direct = {
            key: value
            for key, value in self._recent_direct.items()
            if now - value <= self.ttl_seconds
        }

    def _append(
        self,
        values: dict[tuple[str, str, str], deque[_CorrelationEntry]],
        key: tuple[str, str, str],
        entry: _CorrelationEntry,
    ) -> None:
        values.setdefault(key, deque()).append(entry)
        while sum(len(items) for items in values.values()) > self.max_entries:
            oldest_key = min(
                values,
                key=lambda item: values[item][0].observed_at,
            )
            values[oldest_key].popleft()
            if not values[oldest_key]:
                values.pop(oldest_key, None)

    def _remember_direct(
        self,
        key: tuple[str, str, str],
        observed_at: float,
    ) -> None:
        self._recent_direct[key] = observed_at
        while len(self._recent_direct) > self.max_entries:
            oldest_key = min(self._recent_direct, key=self._recent_direct.get)
            self._recent_direct.pop(oldest_key, None)

    @staticmethod
    def _pop(
        values: dict[tuple[str, str, str], deque[_CorrelationEntry]],
        key: tuple[str, str, str],
    ) -> _CorrelationEntry | None:
        entries = values.get(key)
        if not entries:
            return None
        entry = entries.popleft()
        if not entries:
            values.pop(key, None)
        return entry

    @staticmethod
    def _notification_target(event: TwitchEvent) -> str:
        payload = event.payload.get("event", {})
        if not isinstance(payload, Mapping):
            return ""
        return {
            "sub": "channel.subscribe",
            "resub": "channel.subscription.message",
        }.get(str(payload.get("notice_type", "")), "")

    @staticmethod
    def _key(
        event: TwitchEvent,
        target_type: str,
        *,
        chat_notification: bool = False,
    ) -> tuple[str, str, str]:
        payload = event.payload.get("event", {})
        if not isinstance(payload, Mapping):
            payload = {}
        broadcaster = str(payload.get("broadcaster_user_id", "")).strip()
        user_id = str(
            payload.get("chatter_user_id" if chat_notification else "user_id", "")
            or payload.get("chatter_user_login" if chat_notification else "user_login", "")
        ).strip().casefold()
        return target_type, broadcaster, user_id

    @staticmethod
    def _enrich(direct: TwitchEvent, notification: TwitchEvent) -> TwitchEvent:
        direct_payload = dict(direct.payload)
        direct_event = direct_payload.get("event", {})
        notice_event = notification.payload.get("event", {})
        if not isinstance(direct_event, Mapping) or not isinstance(notice_event, Mapping):
            return direct
        enriched = dict(direct_event)
        notice_type = str(notice_event.get("notice_type", ""))
        details = notice_event.get(notice_type, {})
        if not isinstance(details, Mapping):
            details = {}
        field_map = {
            "sub_tier": "tier",
            "is_prime": "is_prime",
            "is_gift": "is_gift",
            "cumulative_months": "cumulative_months",
            "streak_months": "streak_months",
            "duration_months": "duration_months",
        }
        for source, target in field_map.items():
            if source in details and target not in enriched:
                enriched[target] = details[source]
        message = notice_event.get("message", {})
        if isinstance(message, Mapping) and str(message.get("text", "")):
            enriched["message"] = {"text": str(message["text"])}
        direct_payload["event"] = enriched
        return replace(direct, payload=direct_payload)


@dataclass(slots=True)
class TwitchEventAutomationTrigger:
    trigger_id: str
    routine_id: str
    event_type: str
    filters: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    reset_minutes: int = 15
    reward_id: str = ""
    reward_title: str = ""

    @classmethod
    def from_dict(
        cls, values: Mapping[str, Any]
    ) -> TwitchEventAutomationTrigger:
        raw_filters = values.get("filters", {})
        return cls(
            trigger_id=str(values.get("trigger_id", "")),
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
            reset_minutes=min(
                max(int(values.get("reset_minutes", 15)), 1),
                180,
            ),
            reward_id=str(values.get("reward_id", "")).strip(),
            reward_title=str(values.get("reward_title", "")).strip(),
        )


class TwitchEventTriggerStore:
    VERSION = 3
    FIRST_MESSAGE_STATE_VERSION = 1

    def __init__(
        self,
        path: Path | None = None,
        routine_store: RoutineStore | None = None,
        first_message_state_path: Path | None = None,
    ) -> None:
        self.path = path or user_data_root() / "twitch" / "event_triggers.json"
        self.first_message_state_path = (
            first_message_state_path
            or self.path.with_name("first_message_state.json")
        )
        self.routine_store = routine_store or RoutineStore()
        self.triggers: list[TwitchEventAutomationTrigger] = []
        self._first_message_seen: dict[str, set[str]] = {}
        self._stream_key = ""
        self._offline_since: datetime | None = None

    def load(self) -> list[TwitchEventAutomationTrigger]:
        if not self.routine_store.routines and self.routine_store.path.exists():
            self.routine_store.load()
        if not self.path.exists():
            self.triggers = []
            self._load_first_message_state()
            return []
        payload = load_json_with_backup(self.path)
        if not isinstance(payload, dict):
            raise ValueError("Twitch event triggers must contain a JSON object.")
        version = payload.get("version")
        if type(version) is not int or version != self.VERSION:
            raise ValueError(
                f"Unsupported Twitch event trigger version {version}; "
                f"expected {self.VERSION}."
            )
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
        self._load_first_message_state()
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
        reset_minutes: int = 15,
        reward_id: str = "",
        reward_title: str = "",
    ) -> TwitchEventAutomationTrigger:
        trigger = TwitchEventAutomationTrigger(
            trigger_id=uuid4().hex,
            routine_id=routine_id,
            event_type=event_type.strip(),
            filters=self._clean_filters(filters or {}),
            enabled=bool(enabled),
            reset_minutes=int(reset_minutes),
            reward_id=reward_id.strip(),
            reward_title=reward_title.strip(),
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
        reset_minutes: int | None = None,
        reward_id: str = "",
        reward_title: str = "",
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
            reset_minutes=(
                trigger.reset_minutes
                if reset_minutes is None
                else int(reset_minutes)
            ),
            reward_id=reward_id.strip(),
            reward_title=reward_title.strip(),
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
        previous_seen = self._first_message_seen.pop(trigger.trigger_id, None)
        try:
            self.save()
        except OSError:
            self.triggers.append(trigger)
            self.routine_store.link_trigger(trigger.routine_id, trigger.trigger_id)
            if previous_seen is not None:
                self._first_message_seen[trigger.trigger_id] = previous_seen
            raise
        self._persist_first_message_state()
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
        effective_type = self._automation_event_type(twitch_event)
        context = self.context_for(
            twitch_event,
            event,
            event_type=effective_type,
        )
        if twitch_event.subscription_type == "stream.online":
            self.observe_stream(
                {
                    "id": self._first(event, "id"),
                    "started_at": self._first(event, "started_at"),
                },
                twitch_event.received_at,
            )
        elif twitch_event.subscription_type == "stream.offline":
            self.observe_stream(None, twitch_event.received_at)
        return tuple(
            TriggerEvent(
                trigger_id=trigger.trigger_id,
                service="twitch",
                trigger_type="eventsub",
                context=context,
            )
            for trigger in self.triggers
            if trigger.enabled
            and trigger.event_type == effective_type
            and self._matches_reward(event, trigger)
            and self._matches(event, trigger.filters)
        )

    def add_channel_point_redemption(
        self,
        routine_id: str,
        *,
        reward_id: str = "",
        reward_title: str = "",
        enabled: bool = True,
    ) -> TwitchEventAutomationTrigger:
        return self.add(
            routine_id,
            CHANNEL_POINT_REDEMPTION_EVENT_TYPE,
            enabled=enabled,
            reward_id=reward_id,
            reward_title=reward_title,
        )

    def update_channel_point_redemption(
        self,
        trigger_id: str,
        *,
        reward_id: str = "",
        reward_title: str = "",
        enabled: bool | None = None,
    ) -> TwitchEventAutomationTrigger:
        return self.update(
            trigger_id,
            event_type=CHANNEL_POINT_REDEMPTION_EVENT_TYPE,
            enabled=enabled,
            reward_id=reward_id,
            reward_title=reward_title,
        )

    def evaluate_named(
        self,
        event_type: str,
        context: Mapping[str, object],
        *,
        trigger_type: str = "event",
    ) -> tuple[TriggerEvent, ...]:
        """Publish Hub-derived Twitch events through the normal trigger store."""

        values = {str(key): str(value) for key, value in context.items()}
        return tuple(
            TriggerEvent(
                trigger_id=trigger.trigger_id,
                service="twitch",
                trigger_type=trigger_type,
                context=values,
            )
            for trigger in self.triggers
            if trigger.enabled and trigger.event_type == event_type
        )

    def add_keyword_phrase(
        self,
        routine_id: str,
        phrase: str,
        *,
        match_type: str = "contains",
        ignore_case: bool = True,
        whole_word: bool = True,
        enabled: bool = True,
    ) -> TwitchEventAutomationTrigger:
        return self.add(
            routine_id,
            KEYWORD_PHRASE_EVENT_TYPE,
            filters={
                "phrase": phrase,
                "match_type": match_type,
                "ignore_case": str(bool(ignore_case)).lower(),
                "whole_word": str(bool(whole_word)).lower(),
            },
            enabled=enabled,
        )

    def update_keyword_phrase(
        self,
        trigger_id: str,
        phrase: str,
        *,
        match_type: str = "contains",
        ignore_case: bool = True,
        whole_word: bool = True,
        enabled: bool | None = None,
    ) -> TwitchEventAutomationTrigger:
        return self.update(
            trigger_id,
            event_type=KEYWORD_PHRASE_EVENT_TYPE,
            filters={
                "phrase": phrase,
                "match_type": match_type,
                "ignore_case": str(bool(ignore_case)).lower(),
                "whole_word": str(bool(whole_word)).lower(),
            },
            enabled=enabled,
        )

    def evaluate_keyword_phrase(
        self,
        message: TwitchMessage,
    ) -> tuple[TriggerEvent, ...]:
        matches: list[TriggerEvent] = []
        for trigger in self.triggers:
            if not trigger.enabled or trigger.event_type != KEYWORD_PHRASE_EVENT_TYPE:
                continue
            phrase = trigger.filters.get("phrase", "")
            span = self._keyword_span(
                message.text,
                phrase,
                trigger.filters.get("match_type", "contains"),
                self._filter_bool(trigger.filters, "ignore_case", True),
                self._filter_bool(trigger.filters, "whole_word", True),
            )
            if span is None:
                continue
            start, end = span
            context = self.chat_context_for(message)
            context.update(
                {
                    "keyword.message": message.text,
                    "keyword.match": phrase,
                    "keyword.before": message.text[:start].strip(),
                    "keyword.after": message.text[end:].strip(),
                }
            )
            matches.append(
                TriggerEvent(
                    trigger_id=trigger.trigger_id,
                    service="twitch",
                    trigger_type="keyword_phrase",
                    context=context,
                )
            )
        return tuple(matches)

    def observe_stream(
        self,
        stream: Mapping[str, Any] | None,
        observed_at: datetime | None = None,
    ) -> None:
        now = self._aware(observed_at or datetime.now(timezone.utc))
        if isinstance(stream, Mapping):
            stream_key = self._first(stream, "id", "started_at")
            if self._stream_key and stream_key and stream_key != self._stream_key:
                self._first_message_seen.clear()
            changed = bool(stream_key and stream_key != self._stream_key)
            if self._offline_since is not None:
                changed = self._expire_first_message_state(now) or changed
            self._stream_key = stream_key or self._stream_key or now.isoformat()
            changed = self._offline_since is not None or changed
            self._offline_since = None
            if changed:
                self._persist_first_message_state()
            return
        if self._stream_key and self._offline_since is None:
            self._offline_since = now
            self._persist_first_message_state()

    def evaluate_first_message(
        self,
        message: TwitchMessage,
        *,
        stream_is_live: bool,
        observed_at: datetime | None = None,
    ) -> tuple[TriggerEvent, ...]:
        now = self._aware(observed_at or message.received_at)
        if self._offline_since is not None:
            if self._expire_first_message_state(now):
                self._persist_first_message_state()
        if not stream_is_live and not self._stream_key:
            return ()
        identity = self._message_identity(message)
        if not identity:
            return ()
        event = {
            "user_id": message.user_id,
            "user_name": message.username,
            "user_login": message.user_login,
            "message": message.text,
            "message_id": message.message_id,
            "message_type": message.message_type,
        }
        context = {
            **self.chat_context_for(message),
            "event": "first message",
            "event_type": "channel.chat.first_message",
            "input": message.text or "--",
            "amount": "--",
            "bits": str(message.bits) if message.bits is not None else "--",
            "viewers": "--",
            "tier": "--",
            "reward": "--",
            "reward_id": "--",
            "reward_cost": "--",
            "target_user_id": "--",
            "redemption_id": "--",
            "title": "--",
            "game": "--",
            "uptime": "--",
            "followers": "--",
            "command": "--",
            "command_data": "--",
            "target": "--",
            "uses": "--",
        }
        matches: list[TriggerEvent] = []
        for trigger in self.triggers:
            if (
                not trigger.enabled
                or trigger.event_type != "channel.chat.first_message"
                or not self._matches(event, trigger.filters)
            ):
                continue
            seen = self._first_message_seen.setdefault(trigger.trigger_id, set())
            if identity in seen:
                continue
            seen.add(identity)
            self._persist_first_message_state()
            matches.append(
                TriggerEvent(
                    trigger_id=trigger.trigger_id,
                    service="twitch",
                    trigger_type="first_message",
                    context=context,
                )
            )
        return tuple(matches)

    @staticmethod
    def chat_context_for(message: TwitchMessage) -> dict[str, str]:
        badges = {badge.set_id for badge in message.badges}
        is_broadcaster = "broadcaster" in badges or (
            bool(message.broadcaster_user_id)
            and message.user_id == message.broadcaster_user_id
        )
        context = {
            "user": message.username or "--",
            "user_id": message.user_id or "--",
            "user_login": message.user_login or "--",
            "user_is_mod": str(is_broadcaster or "moderator" in badges).lower(),
            "user_is_subscriber": str(
                is_broadcaster or bool(badges.intersection({"subscriber", "founder"}))
            ).lower(),
            "channel": (
                message.broadcaster_user_name
                or message.broadcaster_user_login
                or "--"
            ),
            "message": message.text or "--",
            "message_id": message.message_id or "--",
        }
        return context

    @staticmethod
    def _filter_bool(
        filters: Mapping[str, str], key: str, default: bool
    ) -> bool:
        value = filters.get(key)
        if value is None:
            return default
        return value.strip().casefold() in {"1", "true", "yes", "on"}

    @staticmethod
    def _keyword_span(
        message: str,
        phrase: str,
        match_type: str,
        ignore_case: bool,
        whole_word: bool,
    ) -> tuple[int, int] | None:
        if not phrase:
            return None
        flags = re.IGNORECASE if ignore_case else 0
        escaped = re.escape(phrase)
        if whole_word:
            escaped = rf"(?<!\w){escaped}(?!\w)"
        anchors = {
            "contains": escaped,
            "exact": rf"^{escaped}$",
            "starts_with": rf"^{escaped}",
            "ends_with": rf"{escaped}$",
        }
        pattern = anchors.get(match_type)
        if pattern is None:
            return None
        match = re.search(pattern, message, flags)
        return match.span() if match is not None else None

    def _expire_first_message_state(self, now: datetime) -> bool:
        if self._offline_since is None:
            return False
        changed = False
        elapsed = now - self._offline_since
        for trigger in self.triggers:
            if (
                trigger.event_type == "channel.chat.first_message"
                and elapsed >= timedelta(minutes=trigger.reset_minutes)
            ):
                if self._first_message_seen.pop(trigger.trigger_id, None) is not None:
                    changed = True
        if not any(
            trigger.event_type == "channel.chat.first_message"
            and elapsed < timedelta(minutes=trigger.reset_minutes)
            for trigger in self.triggers
        ):
            if self._stream_key:
                self._stream_key = ""
                changed = True
        return changed

    @staticmethod
    def _message_identity(message: TwitchMessage) -> str:
        if message.user_id.strip():
            return f"id:{message.user_id.strip()}"
        if message.user_login.strip():
            return f"login:{message.user_login.strip().casefold()}"
        if message.username.strip():
            return f"name:{message.username.strip().casefold()}"
        return ""

    def _load_first_message_state(self) -> None:
        self._first_message_seen = {}
        self._stream_key = ""
        self._offline_since = None
        if not self.first_message_state_path.exists():
            return
        try:
            payload = load_json_with_backup(self.first_message_state_path)
            if not isinstance(payload, Mapping):
                raise ValueError("First Message state must contain an object.")
            version = payload.get("version")
            if type(version) is not int or version != self.FIRST_MESSAGE_STATE_VERSION:
                raise ValueError(
                    f"Unsupported First Message state version {version}; "
                    f"expected {self.FIRST_MESSAGE_STATE_VERSION}."
                )
            stream_id = payload.get("stream_id", "")
            offline_since = payload.get("offline_since", "")
            seen = payload.get("seen_by_trigger", {})
            if not isinstance(stream_id, str) or not isinstance(offline_since, str):
                raise ValueError("First Message stream state is invalid.")
            if not isinstance(seen, Mapping):
                raise ValueError("First Message viewer state is invalid.")
            valid_trigger_ids = {
                trigger.trigger_id
                for trigger in self.triggers
                if trigger.event_type == "channel.chat.first_message"
            }
            loaded_seen: dict[str, set[str]] = {}
            for trigger_id, identities in seen.items():
                if trigger_id not in valid_trigger_ids or not isinstance(identities, list):
                    continue
                values = {
                    str(identity).strip()
                    for identity in identities
                    if str(identity).strip()
                }
                if values:
                    loaded_seen[str(trigger_id)] = values
            parsed_offline = None
            if offline_since:
                parsed_offline = datetime.fromisoformat(
                    offline_since.replace("Z", "+00:00")
                )
            self._stream_key = stream_id.strip()
            self._offline_since = (
                self._aware(parsed_offline) if parsed_offline is not None else None
            )
            self._first_message_seen = loaded_seen if self._stream_key else {}
        except (OSError, TypeError, ValueError) as error:
            self._first_message_seen = {}
            self._stream_key = ""
            self._offline_since = None
            Logger.warning(
                f"Could not load First Message trigger state; reset it: {error}",
                source="TWITCH",
            )

    def _save_first_message_state(self) -> None:
        atomic_write_json(
            self.first_message_state_path,
            {
                "version": self.FIRST_MESSAGE_STATE_VERSION,
                "stream_id": self._stream_key,
                "offline_since": (
                    self._offline_since.isoformat()
                    if self._offline_since is not None
                    else ""
                ),
                "seen_by_trigger": {
                    trigger_id: sorted(identities)
                    for trigger_id, identities in sorted(self._first_message_seen.items())
                    if identities
                },
            },
        )

    def _persist_first_message_state(self) -> None:
        try:
            self._save_first_message_state()
        except OSError as error:
            Logger.warning(
                f"Could not save First Message trigger state: {error}",
                source="TWITCH",
            )

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )

    @classmethod
    def context_for(
        cls,
        twitch_event: TwitchEvent,
        event: Mapping[str, Any],
        *,
        event_type: str | None = None,
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
        raw_message = event.get("message")
        message = (
            str(raw_message.get("text", ""))
            if isinstance(raw_message, Mapping)
            else cls._first(event, "message", "text")
        ) or user_input
        amount = cls._first(event, "bits", "total", "viewers", "amount")
        if not amount:
            amount = str(reward.get("cost", ""))
        context = {
            "user": user or "--",
            "channel": channel or twitch_event.broadcaster_user_login or "--",
            "event": (event_type or twitch_event.subscription_type).rsplit(".", 1)[-1],
            "event_type": event_type or twitch_event.subscription_type,
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
            "user_login": cls._first(
                event,
                "user_login",
                "from_broadcaster_user_login",
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
            "command_data": "--",
            "target": "--",
            "uses": "--",
        }
        if twitch_event.subscription_type == CHANNEL_POINT_REDEMPTION_EVENT_TYPE:
            context.update(
                {
                    "channel_points.redemption_id": cls._first(event, "id"),
                    "channel_points.reward_id": str(reward.get("id", "")),
                    "channel_points.reward_title": str(reward.get("title", "")),
                    "channel_points.reward_cost": str(reward.get("cost", "")),
                    "channel_points.reward_prompt": str(reward.get("prompt", "")),
                    "channel_points.user_input": str(event.get("user_input", "")),
                    "channel_points.status": cls._first(event, "status"),
                    "channel_points.redeemed_at": cls._first(event, "redeemed_at"),
                }
            )
        if twitch_event.subscription_type in {
            "channel.subscribe",
            "channel.subscription.message",
            "channel.subscription.gift",
        }:
            cls._add_subscription_context(context, event)
        if twitch_event.subscription_type == "channel.raid":
            context.update(
                {
                    "raid.direction": (
                        "outgoing"
                        if event_type == "channel.raid.outgoing"
                        else "incoming"
                    ),
                    "raid.source.id": cls._first(event, "from_broadcaster_user_id"),
                    "raid.source.login": cls._first(event, "from_broadcaster_user_login"),
                    "raid.source.name": cls._first(event, "from_broadcaster_user_name"),
                    "raid.target.id": cls._first(event, "to_broadcaster_user_id"),
                    "raid.target.login": cls._first(event, "to_broadcaster_user_login"),
                    "raid.target.name": cls._first(event, "to_broadcaster_user_name"),
                    "raid.viewers": cls._first(event, "viewers"),
                }
            )
        if twitch_event.subscription_type == "channel.cheer" and "is_anonymous" in event:
            context["event.is_anonymous"] = cls._bool_text(event.get("is_anonymous"))
        if twitch_event.subscription_type == "stream.online":
            context.update(
                {
                    "event.stream_id": cls._first(event, "id"),
                    "event.started_at": cls._first(event, "started_at"),
                }
            )
        return context

    @classmethod
    def _add_subscription_context(
        cls,
        context: dict[str, str],
        event: Mapping[str, Any],
    ) -> None:
        fields = {
            "tier": "subscription.tier",
            "cumulative_months": "subscription.cumulative_months",
            "streak_months": "subscription.streak_months",
            "duration_months": "subscription.duration_months",
            "total": "subscription.gift_count",
            "cumulative_total": "subscription.cumulative_gifts",
        }
        for source, target in fields.items():
            if source in event and event.get(source) is not None:
                context[target] = str(event[source])
        for source, target in {
            "is_gift": "subscription.is_gift",
            "is_prime": "subscription.is_prime",
            "is_anonymous": "subscription.is_anonymous",
        }.items():
            if source in event and event.get(source) is not None:
                context[target] = cls._bool_text(event[source])
        raw_message = event.get("message")
        if isinstance(raw_message, Mapping):
            context["subscription.message"] = str(raw_message.get("text", ""))
        elif raw_message is not None:
            context["subscription.message"] = str(raw_message)

    @staticmethod
    def _bool_text(value: object) -> str:
        if isinstance(value, str):
            return str(value.strip().casefold() in {"1", "true", "yes", "on"}).lower()
        return str(bool(value)).lower()

    @staticmethod
    def _automation_event_type(twitch_event: TwitchEvent) -> str:
        if twitch_event.subscription_type != "channel.raid":
            return twitch_event.subscription_type
        subscription = twitch_event.payload.get("subscription", {})
        condition = (
            subscription.get("condition", {})
            if isinstance(subscription, Mapping)
            else {}
        )
        return (
            "channel.raid.outgoing"
            if isinstance(condition, Mapping)
            and condition.get("from_broadcaster_user_id")
            else "channel.raid"
        )

    @staticmethod
    def _matches_reward(
        event: Mapping[str, Any], trigger: TwitchEventAutomationTrigger
    ) -> bool:
        if trigger.event_type != CHANNEL_POINT_REDEMPTION_EVENT_TYPE:
            return True
        if not trigger.reward_id:
            return True
        reward = event.get("reward", {})
        return isinstance(reward, Mapping) and str(reward.get("id", "")) == trigger.reward_id

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
        if trigger.event_type == KEYWORD_PHRASE_EVENT_TYPE:
            phrase = trigger.filters.get("phrase", "").strip()
            if not phrase:
                raise ValueError("Keyword / Phrase triggers require text to match.")
            if len(phrase) > 500:
                raise ValueError("Keyword / Phrase text is limited to 500 characters.")
            if trigger.filters.get("match_type", "contains") not in KEYWORD_MATCH_TYPES:
                raise ValueError("Unknown Keyword / Phrase match type.")
        if not 1 <= trigger.reset_minutes <= 180:
            raise ValueError("First-message reset must be between 1 and 180 minutes.")
        for path in trigger.filters:
            if any(not segment.strip() for segment in path.split(".")):
                raise ValueError("Twitch event filter paths cannot be empty.")
