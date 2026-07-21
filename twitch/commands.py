from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Mapping
from uuid import uuid4

from automation.models import TriggerEvent
from automation.routines import RoutineStore
from core.json_store import atomic_write_json, load_json_with_backup
from core.paths import user_data_root
from twitch.models import TwitchMessage
from twitch.tasks import SendTwitchChatMessageTask


class TwitchCommandPermission(StrEnum):
    EVERYONE = "everyone"
    SUBSCRIBER = "subscriber"
    VIP = "vip"
    MODERATOR = "moderator"
    BROADCASTER = "broadcaster"


class TwitchCommandTriggerOutcome(StrEnum):
    NOT_A_COMMAND = "not_a_command"
    NOT_FOUND = "not_found"
    DISABLED = "disabled"
    DENIED = "denied"
    COOLDOWN = "cooldown"
    READY = "ready"
    TASK_FAILED = "task_failed"


@dataclass(slots=True)
class TwitchCommandTrigger:
    trigger_id: str
    routine_id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    permission: str = TwitchCommandPermission.EVERYONE.value
    global_cooldown_seconds: int = 10
    user_cooldown_seconds: int = 30
    enabled: bool = True
    uses: int = 0
    last_used_at: str = ""
    has_chat_response: bool = True

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> TwitchCommandTrigger:
        trigger_id = (
            str(values.get("trigger_id", ""))
            or str(values.get("command_id", ""))
            or uuid4().hex
        )
        return cls(
            trigger_id=trigger_id,
            routine_id=str(values.get("routine_id", "")),
            name=str(values.get("name", "")),
            aliases=[str(value) for value in values.get("aliases", [])],
            permission=str(
                values.get(
                    "permission", TwitchCommandPermission.EVERYONE.value
                )
            ),
            global_cooldown_seconds=int(
                values.get("global_cooldown_seconds", 10)
            ),
            user_cooldown_seconds=int(
                values.get("user_cooldown_seconds", 30)
            ),
            enabled=bool(values.get("enabled", True)),
            uses=max(int(values.get("uses", 0)), 0),
            last_used_at=str(values.get("last_used_at", "")),
            has_chat_response=bool(values.get("has_chat_response", True)),
        )


@dataclass(frozen=True, slots=True)
class TwitchCommandTriggerResult:
    outcome: TwitchCommandTriggerOutcome
    invocation: str = ""
    trigger_id: str = ""
    routine_id: str = ""
    context: Mapping[str, str] = field(default_factory=dict)
    remaining_seconds: int = 0

    @property
    def handled(self) -> bool:
        return self.outcome not in {
            TwitchCommandTriggerOutcome.NOT_A_COMMAND,
            TwitchCommandTriggerOutcome.NOT_FOUND,
        }

    def to_event(self) -> TriggerEvent:
        if self.outcome is not TwitchCommandTriggerOutcome.READY:
            raise ValueError("Only ready command triggers can be published.")
        return TriggerEvent(
            trigger_id=self.trigger_id,
            service="twitch",
            trigger_type="command",
            context=self.context,
        )


class TwitchCommandTriggerStore:
    VERSION = 3
    MANAGED_BY = "twitch.command"
    NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,24}$")
    RESERVED_NAMES = frozenset({"sallymemory", "sallytrain"})

    def __init__(
        self,
        path: Path | None = None,
        routine_store: RoutineStore | None = None,
    ) -> None:
        self.path = path or user_data_root() / "twitch" / "commands.json"
        self.routine_store = routine_store or RoutineStore()
        self.triggers: list[TwitchCommandTrigger] = []

    def load(self) -> list[TwitchCommandTrigger]:
        self.routine_store.load()
        if not self.path.exists():
            self.triggers = []
            return []
        payload = load_json_with_backup(self.path)
        if not isinstance(payload, dict):
            raise ValueError("Twitch command triggers must contain a JSON object.")
        version = int(payload.get("version", 1))
        if version > self.VERSION:
            raise ValueError("Twitch command trigger data is newer than this app.")
        values = payload.get("triggers", payload.get("commands", []))
        if not isinstance(values, list):
            raise ValueError("Twitch command triggers must contain a trigger list.")
        loaded: list[TwitchCommandTrigger] = []
        self.triggers = loaded
        migrated = False
        for value in values:
            if not isinstance(value, dict):
                continue
            try:
                trigger = TwitchCommandTrigger.from_dict(value)
                response = self._validated_response(value.get("response", ""))
                if not trigger.routine_id:
                    trigger.has_chat_response = bool(response)
                    routine = self._create_routine(trigger, response)
                    trigger.routine_id = routine.routine_id
                    migrated = True
                self._validate_trigger(trigger)
                self._validate_routine(trigger)
            except (TypeError, ValueError):
                continue
            loaded.append(trigger)
        if migrated or version < self.VERSION:
            self.save()
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
        name: str,
        response: str = "",
        *,
        aliases: list[str] | tuple[str, ...] = (),
        permission: str = TwitchCommandPermission.EVERYONE.value,
        global_cooldown_seconds: int = 10,
        user_cooldown_seconds: int = 30,
    ) -> TwitchCommandTrigger:
        response = self._validated_response(response)
        trigger = TwitchCommandTrigger(
            trigger_id=uuid4().hex,
            routine_id="",
            name=self.normalize_name(name),
            aliases=[self.normalize_name(alias) for alias in aliases if alias.strip()],
            permission=str(permission).casefold(),
            global_cooldown_seconds=int(global_cooldown_seconds),
            user_cooldown_seconds=int(user_cooldown_seconds),
            has_chat_response=bool(response),
        )
        self._validate_trigger(trigger)
        routine = self._create_routine(trigger, response)
        trigger.routine_id = routine.routine_id
        self.triggers.append(trigger)
        try:
            self.save()
        except OSError:
            self.triggers.remove(trigger)
            self.routine_store.delete_managed(
                routine.routine_id, self.MANAGED_BY
            )
            raise
        return trigger

    def attach_routine(
        self,
        routine_id: str,
        name: str,
        response: str = "",
        *,
        aliases: list[str] | tuple[str, ...] = (),
        permission: str = TwitchCommandPermission.EVERYONE.value,
        global_cooldown_seconds: int = 10,
        user_cooldown_seconds: int = 30,
    ) -> TwitchCommandTrigger:
        response = self._validated_response(response)
        trigger = TwitchCommandTrigger(
            trigger_id=uuid4().hex,
            routine_id=routine_id,
            name=self.normalize_name(name),
            aliases=[self.normalize_name(alias) for alias in aliases if alias.strip()],
            permission=str(permission).casefold(),
            global_cooldown_seconds=int(global_cooldown_seconds),
            user_cooldown_seconds=int(user_cooldown_seconds),
            has_chat_response=bool(response),
        )
        self._validate_trigger(trigger)
        self.routine_store.attach_managed(
            routine_id,
            trigger_id=trigger.trigger_id,
            managed_by=self.MANAGED_BY,
            task_type=SendTwitchChatMessageTask.task_type,
            task_name="Send Twitch chat response",
            task_config=(
                {"message": response, "as_bot": True}
                if response
                else None
            ),
        )
        self.triggers.append(trigger)
        try:
            self.save()
        except OSError:
            self.triggers.remove(trigger)
            self.routine_store.detach_managed(routine_id, self.MANAGED_BY)
            raise
        return trigger

    def update(
        self,
        trigger_id: str,
        *,
        name: str,
        response: str,
        aliases: list[str] | tuple[str, ...],
        permission: str,
        global_cooldown_seconds: int,
        user_cooldown_seconds: int,
    ) -> TwitchCommandTrigger:
        trigger = self.get(trigger_id)
        if trigger is None:
            raise ValueError("The selected Twitch command trigger no longer exists.")
        response = self._validated_response(response)
        candidate = TwitchCommandTrigger(
            trigger_id=trigger.trigger_id,
            routine_id=trigger.routine_id,
            name=self.normalize_name(name),
            aliases=[self.normalize_name(alias) for alias in aliases if alias.strip()],
            permission=str(permission).casefold(),
            global_cooldown_seconds=int(global_cooldown_seconds),
            user_cooldown_seconds=int(user_cooldown_seconds),
            enabled=trigger.enabled,
            uses=trigger.uses,
            last_used_at=trigger.last_used_at,
            has_chat_response=bool(response),
        )
        self._validate_trigger(candidate, excluding_id=trigger_id)
        routine = self.routine_store.get(trigger.routine_id)
        managed_name = f"Command !{candidate.name}"
        if routine is not None and routine.name != f"Command !{trigger.name}":
            managed_name = routine.name
        if response or trigger.has_chat_response:
            self.routine_store.update_managed_task(
                trigger.routine_id,
                name=managed_name,
                managed_by=self.MANAGED_BY,
                task_type=SendTwitchChatMessageTask.task_type,
                task_name="Send Twitch chat response",
                task_config=(
                    {"message": response, "as_bot": True}
                    if response
                    else None
                ),
            )
        else:
            self.routine_store.update(trigger.routine_id, name=managed_name)
        index = self.triggers.index(trigger)
        self.triggers[index] = candidate
        self.save()
        return candidate

    def delete(self, trigger_id: str, *, delete_routine: bool = True) -> bool:
        trigger = self.get(trigger_id)
        if trigger is None:
            return False
        self.triggers.remove(trigger)
        if delete_routine:
            self.routine_store.delete_managed(trigger.routine_id, self.MANAGED_BY)
        else:
            self.routine_store.detach_managed(trigger.routine_id, self.MANAGED_BY)
        self.save()
        return True

    def for_routine(self, routine_id: str) -> TwitchCommandTrigger | None:
        return next(
            (trigger for trigger in self.triggers if trigger.routine_id == routine_id),
            None,
        )

    def set_enabled(self, trigger_id: str, enabled: bool) -> bool:
        trigger = self.get(trigger_id)
        if trigger is None:
            return False
        trigger.enabled = bool(enabled)
        self.save()
        return True

    def record_use(self, trigger_id: str) -> None:
        trigger = self.get(trigger_id)
        if trigger is None:
            return
        trigger.uses += 1
        trigger.last_used_at = datetime.now(timezone.utc).isoformat()
        self.save()

    def response_for(self, trigger: TwitchCommandTrigger) -> str:
        if not trigger.has_chat_response:
            return ""
        routine = self.routine_store.get(trigger.routine_id)
        if routine is None:
            return ""
        task = self.routine_store.managed_task(
            routine,
            self.MANAGED_BY,
            SendTwitchChatMessageTask.task_type,
        )
        if task is None:
            return ""
        return str(task.config.get("message", ""))

    def get(self, trigger_id: str) -> TwitchCommandTrigger | None:
        return next(
            (
                trigger
                for trigger in self.triggers
                if trigger.trigger_id == trigger_id
            ),
            None,
        )

    def resolve(self, invocation: str) -> TwitchCommandTrigger | None:
        clean = self.normalize_name(invocation)
        return next(
            (
                trigger
                for trigger in self.triggers
                if clean == trigger.name or clean in trigger.aliases
            ),
            None,
        )

    @classmethod
    def normalize_name(cls, name: str) -> str:
        return name.strip().casefold().removeprefix("!")

    def _create_routine(
        self,
        trigger: TwitchCommandTrigger,
        response: str,
    ):
        return self.routine_store.create_managed(
            trigger_id=trigger.trigger_id,
            name=f"Command !{trigger.name}",
            managed_by=self.MANAGED_BY,
            task_type=SendTwitchChatMessageTask.task_type,
            task_name="Send Twitch chat response",
            task_config=(
                {"message": response, "as_bot": True}
                if response
                else None
            ),
        )

    def _validate_routine(self, trigger: TwitchCommandTrigger) -> None:
        routine = self.routine_store.get(trigger.routine_id)
        if (
            routine is None
            or routine.trigger_id != trigger.trigger_id
            or routine.managed_by != self.MANAGED_BY
        ):
            raise ValueError("The Twitch command trigger has no valid managed routine.")
        task = self.routine_store.managed_task(
            routine,
            self.MANAGED_BY,
            SendTwitchChatMessageTask.task_type,
        )
        if trigger.has_chat_response and task is None:
            raise ValueError("The Twitch command trigger has no response task.")
        if task is not None:
            SendTwitchChatMessageTask.validate_template(
                str(task.config.get("message", ""))
            )

    @staticmethod
    def _validated_response(value: object) -> str:
        response = str(value or "").strip()
        if response:
            SendTwitchChatMessageTask.validate_template(response)
        return response

    def _validate_trigger(
        self,
        trigger: TwitchCommandTrigger,
        *,
        excluding_id: str = "",
    ) -> None:
        names = [trigger.name, *trigger.aliases]
        if not self.NAME_PATTERN.fullmatch(trigger.name):
            raise ValueError(
                "Command names must use 1-25 lowercase letters, numbers, hyphens, or underscores."
            )
        if any(not self.NAME_PATTERN.fullmatch(alias) for alias in trigger.aliases):
            raise ValueError("Every alternate command must be a valid command name.")
        if len(set(names)) != len(names):
            raise ValueError(
                "The primary and alternate command names must be unique."
            )
        reserved = self.RESERVED_NAMES.intersection(names)
        if reserved:
            raise ValueError(
                f"!{sorted(reserved)[0]} is a built-in Sally command."
            )
        occupied = {
            name
            for existing in self.triggers
            if existing.trigger_id != excluding_id
            for name in [existing.name, *existing.aliases]
        }
        overlap = occupied.intersection(names)
        if overlap:
            raise ValueError(f"Command name already used: !{sorted(overlap)[0]}")
        try:
            TwitchCommandPermission(trigger.permission)
        except ValueError as error:
            raise ValueError("Unknown Twitch command permission.") from error
        if not 0 <= trigger.global_cooldown_seconds <= 3600:
            raise ValueError("Global cooldown must be between 0 and 3600 seconds.")
        if not 0 <= trigger.user_cooldown_seconds <= 86400:
            raise ValueError("Viewer cooldown must be between 0 and 86400 seconds.")


class TwitchCommandTriggerDispatcher:
    def __init__(
        self,
        store: TwitchCommandTriggerStore,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.store = store
        self.clock = clock
        self._global_uses: dict[str, float] = {}
        self._viewer_uses: dict[tuple[str, str], float] = {}

    def evaluate(
        self,
        message: TwitchMessage,
        context: Mapping[str, object] | None = None,
    ) -> TwitchCommandTriggerResult:
        text = message.text.strip()
        if not text.startswith("!"):
            return TwitchCommandTriggerResult(
                TwitchCommandTriggerOutcome.NOT_A_COMMAND
            )
        command_text = text[1:].strip()
        if not command_text:
            return TwitchCommandTriggerResult(
                TwitchCommandTriggerOutcome.NOT_FOUND
            )
        parts = command_text.split(maxsplit=1)
        invocation = parts[0].casefold()
        arguments = parts[1].strip() if len(parts) > 1 else ""
        trigger = self.store.resolve(invocation)
        if trigger is None:
            return TwitchCommandTriggerResult(
                TwitchCommandTriggerOutcome.NOT_FOUND,
                invocation=invocation,
            )
        base = {
            "invocation": invocation,
            "trigger_id": trigger.trigger_id,
            "routine_id": trigger.routine_id,
        }
        if not trigger.enabled:
            return TwitchCommandTriggerResult(
                TwitchCommandTriggerOutcome.DISABLED, **base
            )
        if not self._has_permission(trigger.permission, message):
            return TwitchCommandTriggerResult(
                TwitchCommandTriggerOutcome.DENIED, **base
            )
        now = self.clock()
        global_remaining = trigger.global_cooldown_seconds - (
            now - self._global_uses.get(trigger.trigger_id, -1e12)
        )
        viewer_key = (trigger.trigger_id, message.user_id or message.user_login)
        viewer_remaining = trigger.user_cooldown_seconds - (
            now - self._viewer_uses.get(viewer_key, -1e12)
        )
        remaining = max(global_remaining, viewer_remaining)
        if remaining > 0:
            return TwitchCommandTriggerResult(
                TwitchCommandTriggerOutcome.COOLDOWN,
                remaining_seconds=max(1, int(remaining + 0.999)),
                **base,
            )
        values = {
            key: str(value)
            for key, value in (context or {}).items()
            if key in SendTwitchChatMessageTask.TEMPLATE_VARIABLES
        }
        values.update(
            {
                "user": message.username,
                "user_id": message.user_id or "--",
                "target_user_id": "--",
                "message_id": message.message_id or "--",
                "redemption_id": "--",
                "command": trigger.name,
                "args": arguments,
                "target": (
                    arguments.split(maxsplit=1)[0].lstrip("@")
                    if arguments
                    else "--"
                ),
                "uses": str(trigger.uses + 1),
            }
        )
        for variable in SendTwitchChatMessageTask.TEMPLATE_VARIABLES:
            values.setdefault(variable, "--")
        return TwitchCommandTriggerResult(
            TwitchCommandTriggerOutcome.READY,
            context=values,
            **base,
        )

    def record_executed(
        self,
        result: TwitchCommandTriggerResult,
        message: TwitchMessage,
    ) -> None:
        if result.outcome is not TwitchCommandTriggerOutcome.READY:
            return
        now = self.clock()
        self._global_uses[result.trigger_id] = now
        viewer_key = (result.trigger_id, message.user_id or message.user_login)
        self._viewer_uses[viewer_key] = now
        self.store.record_use(result.trigger_id)

    @staticmethod
    def _has_permission(permission: str, message: TwitchMessage) -> bool:
        badges = {badge.set_id for badge in message.badges}
        broadcaster = "broadcaster" in badges or (
            bool(message.broadcaster_user_id)
            and message.user_id == message.broadcaster_user_id
        )
        moderator = broadcaster or "moderator" in badges
        vip = moderator or "vip" in badges
        subscriber = vip or "subscriber" in badges or "founder" in badges
        return {
            TwitchCommandPermission.EVERYONE.value: True,
            TwitchCommandPermission.SUBSCRIBER.value: subscriber,
            TwitchCommandPermission.VIP.value: vip,
            TwitchCommandPermission.MODERATOR.value: moderator,
            TwitchCommandPermission.BROADCASTER.value: broadcaster,
        }.get(permission, False)
