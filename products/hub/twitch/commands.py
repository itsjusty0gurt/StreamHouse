from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Mapping
from uuid import uuid4

from products.hub.automation.variable_outputs import generated_output_definitions
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.automation.models import RoutineDefinition, TriggerEvent
from products.hub.automation.routines import RoutineStore
from shared.streamhouse_runtime.json_store import atomic_write_json, load_json_with_backup
from shared.streamhouse_runtime.paths import user_data_root
from products.hub.twitch.models import TwitchMessage
from products.hub.twitch.tasks import SendTwitchChatMessageTask
from products.hub.twitch.channel_information import (
    CHANNEL_INFORMATION_FIELD_LABELS,
    ChannelInformationStore,
)
from products.hub.twitch.default_commands import (
    DefaultCommandDefinition,
    default_command_order,
    default_command_definitions,
)


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
    CONFIGURATION_ERROR = "configuration_error"


class TwitchCommandSetupState(StrEnum):
    SETUP_REQUIRED = "Setup Required"
    READY_DISABLED = "Ready but Disabled"
    ENABLED = "Enabled"
    CONFIGURATION_ERROR = "Configuration Error"


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
    default_id: str = ""

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
            default_id=str(values.get("default_id", "")),
        )

    @property
    def is_default(self) -> bool:
        return bool(self.default_id)


@dataclass(frozen=True, slots=True)
class DefaultCommandSeedResult:
    created: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()


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
    VERSION = 5
    MANAGED_BY = "twitch.command"
    NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,24}$")
    RESERVED_NAMES = frozenset({"sallymemory", "sallytrain"})

    def __init__(
        self,
        path: Path | None = None,
        routine_store: RoutineStore | None = None,
        variable_registry: VariableRegistry | None = None,
    ) -> None:
        self.path = path or user_data_root() / "twitch" / "commands.json"
        self.routine_store = routine_store or RoutineStore()
        self.variable_registry = variable_registry
        self.triggers: list[TwitchCommandTrigger] = []
        self.removed_default_ids: set[str] = set()
        self.default_seed_conflicts: tuple[str, ...] = ()

    def load(self) -> list[TwitchCommandTrigger]:
        self.routine_store.load()
        if not self.path.exists():
            self.triggers = []
            self.removed_default_ids = set()
            return []
        payload = load_json_with_backup(self.path)
        if not isinstance(payload, dict):
            raise ValueError("Twitch command triggers must contain a JSON object.")
        version = int(payload.get("version", 0))
        if version != self.VERSION:
            raise ValueError(
                "Twitch command data uses a discarded pre-alpha schema and must be reset."
            )
        values = payload.get("triggers", [])
        if not isinstance(values, list):
            raise ValueError("Twitch command triggers must contain a trigger list.")
        loaded: list[TwitchCommandTrigger] = []
        self.triggers = loaded
        removed = payload.get("removed_default_ids", [])
        self.removed_default_ids = {
            str(value) for value in removed if str(value).strip()
        } if isinstance(removed, list) else set()
        for value in values:
            if not isinstance(value, dict):
                continue
            try:
                trigger = TwitchCommandTrigger.from_dict(value)
                if not trigger.routine_id:
                    raise ValueError("Twitch command trigger is missing its routine.")
                self._validate_trigger(trigger)
                self._validate_routine(trigger)
            except (TypeError, ValueError):
                continue
            loaded.append(trigger)
        return list(loaded)

    def save(self) -> None:
        atomic_write_json(
            self.path,
            {
                "version": self.VERSION,
                "triggers": [asdict(trigger) for trigger in self.triggers],
                "removed_default_ids": sorted(self.removed_default_ids),
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
            default_id="",
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
            default_id="",
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
            default_id=trigger.default_id,
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
        if trigger.default_id:
            self.removed_default_ids.add(trigger.default_id)
        if delete_routine:
            self.routine_store.delete_managed(trigger.routine_id, self.MANAGED_BY)
        else:
            self.routine_store.detach_managed(trigger.routine_id, self.MANAGED_BY)
        self.save()
        return True

    def seed_default_commands(
        self,
        *,
        restore_removed: bool = False,
    ) -> DefaultCommandSeedResult:
        created: list[str] = []
        conflicts: list[str] = []
        for definition in default_command_definitions():
            if self.default(definition.default_id) is not None:
                continue
            if definition.default_id in self.removed_default_ids and not restore_removed:
                continue
            conflict = self._default_conflict(definition)
            if conflict:
                conflicts.append(conflict)
                continue
            self._install_default(definition)
            self.removed_default_ids.discard(definition.default_id)
            created.append(definition.name)
        if created:
            self.save()
        self.default_seed_conflicts = tuple(conflicts)
        return DefaultCommandSeedResult(tuple(created), tuple(conflicts))

    def restore_default_commands(self) -> DefaultCommandSeedResult:
        return self.seed_default_commands(restore_removed=True)

    def reset_default(self, default_id: str) -> TwitchCommandTrigger:
        definition = self._default_definition(default_id)
        existing = self.default(default_id)
        if existing is None:
            conflict = self._default_conflict(definition)
            if conflict:
                raise ValueError(conflict)
            self._install_default(definition)
            self.removed_default_ids.discard(default_id)
            self.save()
            return self.default(default_id)  # type: ignore[return-value]
        occupied = {
            name
            for trigger in self.triggers
            if trigger.trigger_id != existing.trigger_id
            for name in (trigger.name, *trigger.aliases)
        }
        if definition.name in occupied:
            raise ValueError(
                f"Could not reset !{definition.name}: that name is used by another command."
            )
        routine = self.routine_store.get(existing.routine_id)
        group_id = routine.group_id if routine is not None else ""
        replacement = self._routine_for(definition, group_id=group_id)
        routines = [
            replacement if value.routine_id == existing.routine_id else value
            for value in self.routine_store.routines
        ]
        if routine is None:
            routines.append(replacement)
        self.routine_store.routines = routines
        self.routine_store.save()
        replacement_trigger = self._trigger_for(
            definition,
            uses=existing.uses,
            last_used_at=existing.last_used_at,
        )
        self.triggers[self.triggers.index(existing)] = replacement_trigger
        self.removed_default_ids.discard(default_id)
        self.save()
        return replacement_trigger

    def default(self, default_id: str) -> TwitchCommandTrigger | None:
        return next(
            (trigger for trigger in self.triggers if trigger.default_id == default_id),
            None,
        )

    @staticmethod
    def _default_definition(default_id: str) -> DefaultCommandDefinition:
        definition = next(
            (value for value in default_command_definitions() if value.default_id == default_id),
            None,
        )
        if definition is None:
            raise ValueError("Unknown Streamhouse default command.")
        return definition

    def _default_conflict(self, definition: DefaultCommandDefinition) -> str:
        if self.resolve(definition.name) is not None:
            return f"Could not restore !{definition.name}: that name is used by a custom command."
        if self.get(definition.trigger_id) is not None:
            return f"Could not restore !{definition.name}: its trigger ID is already in use."
        if self.routine_store.get(definition.routine_id) is not None:
            return f"Could not restore !{definition.name}: its routine ID is already in use."
        return ""

    def _install_default(self, definition: DefaultCommandDefinition) -> None:
        trigger = self._trigger_for(definition)
        routine = self._routine_for(definition)
        self._validate_trigger(trigger)
        self.routine_store.routines.append(routine)
        self.triggers.append(trigger)
        try:
            self.routine_store.save()
        except Exception:
            self.routine_store.routines.remove(routine)
            self.triggers.remove(trigger)
            raise

    def _trigger_for(
        self,
        definition: DefaultCommandDefinition,
        *,
        uses: int = 0,
        last_used_at: str = "",
    ) -> TwitchCommandTrigger:
        return TwitchCommandTrigger(
            trigger_id=definition.trigger_id,
            routine_id=definition.routine_id,
            name=definition.name,
            permission=TwitchCommandPermission.EVERYONE.value,
            global_cooldown_seconds=definition.global_cooldown_seconds,
            user_cooldown_seconds=definition.user_cooldown_seconds,
            enabled=definition.enabled,
            uses=uses,
            last_used_at=last_used_at,
            has_chat_response=True,
            default_id=definition.default_id,
        )

    def _routine_for(
        self,
        definition: DefaultCommandDefinition,
        *,
        group_id: str = "",
    ) -> RoutineDefinition:
        return RoutineDefinition(
            routine_id=definition.routine_id,
            name=f"Command !{definition.name}",
            trigger_id=definition.trigger_id,
            tasks=deepcopy(list(definition.tasks)),
            managed_by=self.MANAGED_BY,
            group_id=group_id,
        )

    def for_routine(self, routine_id: str) -> TwitchCommandTrigger | None:
        return next(
            (trigger for trigger in self.triggers if trigger.routine_id == routine_id),
            None,
        )

    def ordered_triggers(self, filter_text: str = "") -> list[TwitchCommandTrigger]:
        query = filter_text.strip().casefold().removeprefix("!")
        matches = [
            trigger
            for trigger in self.triggers
            if not query
            or query in trigger.name.casefold()
            or any(query in alias.casefold() for alias in trigger.aliases)
            or query in ("default" if trigger.is_default else "custom")
        ]
        order = default_command_order()
        defaults = sorted(
            (trigger for trigger in matches if trigger.is_default),
            key=lambda trigger: (order.get(trigger.default_id, 10_000), trigger.name),
        )
        customs = sorted(
            (trigger for trigger in matches if not trigger.is_default),
            key=lambda trigger: (trigger.name.casefold(), trigger.trigger_id),
        )
        return [*defaults, *customs]

    def setup_state(
        self,
        trigger: TwitchCommandTrigger,
        channel_information: ChannelInformationStore,
    ) -> TwitchCommandSetupState:
        requirement = self.setup_requirement(trigger.default_id)
        if not requirement:
            return (
                TwitchCommandSetupState.ENABLED
                if trigger.enabled
                else TwitchCommandSetupState.READY_DISABLED
            )
        available = (
            bool(channel_information.usable_social_links())
            if requirement == "socials"
            else channel_information.field_available(requirement)
        )
        if available:
            return (
                TwitchCommandSetupState.ENABLED
                if trigger.enabled
                else TwitchCommandSetupState.READY_DISABLED
            )
        return (
            TwitchCommandSetupState.CONFIGURATION_ERROR
            if trigger.enabled
            else TwitchCommandSetupState.SETUP_REQUIRED
        )

    @staticmethod
    def setup_requirement(default_id: str) -> str:
        definition = next(
            (
                value
                for value in default_command_definitions()
                if value.default_id == default_id
            ),
            None,
        )
        return definition.setup_requirement if definition is not None else ""

    @classmethod
    def setup_requirement_label(cls, default_id: str) -> str:
        requirement = cls.setup_requirement(default_id)
        if requirement == "socials":
            return "at least one checked, valid social link"
        return CHANNEL_INFORMATION_FIELD_LABELS.get(requirement, "")

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
            generated_definitions = tuple(
                definition
                for candidate in routine.tasks
                for definition in generated_output_definitions(
                    candidate.task_type,
                    candidate.config,
                )
            )
            SendTwitchChatMessageTask.validate_template(
                str(task.config.get("message", "")),
                registry=self.variable_registry,
                extra_definitions=generated_definitions,
            )

    def _validated_response(self, value: object) -> str:
        response = str(value or "").strip()
        if response:
            SendTwitchChatMessageTask.validate_template(
                response, registry=self.variable_registry
            )
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
        channel_information: ChannelInformationStore | None = None,
    ) -> None:
        self.store = store
        self.clock = clock
        self.channel_information = channel_information
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
        requirement = self.store.setup_requirement(trigger.default_id)
        if requirement and self.channel_information is not None:
            available = (
                bool(self.channel_information.usable_social_links())
                if requirement == "socials"
                else self.channel_information.field_available(requirement)
            )
            if not available:
                return TwitchCommandTriggerResult(
                    TwitchCommandTriggerOutcome.CONFIGURATION_ERROR,
                    **base,
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
        }
        values.update(
            {
                "user": message.username,
                "user_id": message.user_id or "--",
                "user_login": message.user_login or "--",
                "user_is_mod": str(
                    any(
                        badge.set_id in {"moderator", "broadcaster"}
                        for badge in message.badges
                    )
                ).lower(),
                "user_is_subscriber": str(
                    any(badge.set_id == "subscriber" for badge in message.badges)
                ).lower(),
                "viewer_permission": self._permission_level(message),
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

    @staticmethod
    def _permission_level(message: TwitchMessage) -> str:
        badges = {badge.set_id for badge in message.badges}
        broadcaster = "broadcaster" in badges or (
            bool(message.broadcaster_user_id)
            and message.user_id == message.broadcaster_user_id
        )
        if broadcaster:
            return TwitchCommandPermission.BROADCASTER.value
        if "moderator" in badges:
            return TwitchCommandPermission.MODERATOR.value
        if "vip" in badges:
            return TwitchCommandPermission.VIP.value
        if "subscriber" in badges or "founder" in badges:
            return TwitchCommandPermission.SUBSCRIBER.value
        return TwitchCommandPermission.EVERYONE.value
