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
from products.hub.automation.variable_registry import VariableDefinition, VariableRegistry
from products.hub.automation.models import RoutineDefinition, RoutineGroup, TriggerEvent
from products.hub.automation.routines import RoutineStore
from shared.streamhouse_runtime.json_store import atomic_write_json, load_json_with_backup
from shared.streamhouse_runtime.paths import user_data_root
from products.hub.twitch.models import TwitchMessage
from products.hub.twitch.tasks import SendTwitchChatMessageTask
from products.hub.twitch.channel_information import (
    CHANNEL_INFORMATION_FIELD_LABELS,
    SOCIAL_SERVICE_LABELS,
    ChannelInformationStore,
    SocialLink,
    normalize_social_url,
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
    VERSION = 6
    MANAGED_BY = "twitch.command"
    COMMANDS_GROUP_NAME = "Commands"
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

    def load(self) -> list[TwitchCommandTrigger]:
        self.routine_store.load()
        if not self.path.exists():
            self.triggers = []
            self.reconcile_managed_routines()
            return []
        payload = load_json_with_backup(self.path)
        if not isinstance(payload, dict):
            raise ValueError("Twitch command triggers must contain a JSON object.")
        version = int(payload.get("version", 0))
        if version not in {5, self.VERSION}:
            raise ValueError(
                "Twitch command data uses a discarded pre-alpha schema and must be reset."
            )
        values = payload.get("triggers", [])
        if not isinstance(values, list):
            raise ValueError("Twitch command triggers must contain a trigger list.")
        loaded: list[TwitchCommandTrigger] = []
        self.triggers = loaded
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
        if version == 5:
            self._remove_pristine_seeded_defaults()
        self.reconcile_managed_routines()
        if version == 5:
            self.save()
        return list(loaded)

    def reconcile_managed_routines(self) -> tuple[int, int]:
        """Release routines whose owning command no longer exists.

        Custom routines keep their tasks and become ordinary routines so they
        can be edited, assigned a new trigger, or deleted. Orphaned built-in
        routines are removed because default templates do not own routines.
        """
        active = {
            trigger.routine_id: trigger.trigger_id
            for trigger in self.triggers
        }
        default_routine_ids = {
            definition.routine_id for definition in default_command_definitions()
        }
        detached = 0
        deleted = 0
        for routine in tuple(self.routine_store.routines):
            if routine.managed_by != self.MANAGED_BY:
                continue
            if active.get(routine.routine_id) == routine.trigger_id:
                continue
            if routine.routine_id in default_routine_ids:
                self.routine_store.delete_managed(routine.routine_id, self.MANAGED_BY)
                deleted += 1
            else:
                self.routine_store.detach_managed(routine.routine_id, self.MANAGED_BY)
                self.routine_store.update(routine.routine_id, group_id="")
                detached += 1
        if self.triggers:
            group_id = self._ensure_commands_group()
            for trigger in self.triggers:
                routine = self.routine_store.get(trigger.routine_id)
                if routine is not None and routine.group_id != group_id:
                    self.routine_store.update(routine.routine_id, group_id=group_id)
        else:
            self._remove_empty_commands_group()
        return detached, deleted

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
            self._remove_empty_commands_group()
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
        routine = self.routine_store.get(routine_id)
        if (
            routine is not None
            and routine.managed_by == self.MANAGED_BY
            and self.for_routine(routine_id) is None
        ):
            self.routine_store.detach_managed(routine_id, self.MANAGED_BY)
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
        self.routine_store.update(
            routine_id,
            group_id=self._ensure_commands_group(),
        )
        self.triggers.append(trigger)
        try:
            self.save()
        except OSError:
            self.triggers.remove(trigger)
            self.routine_store.detach_managed(routine_id, self.MANAGED_BY)
            self.routine_store.update(routine_id, group_id="")
            self._remove_empty_commands_group()
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
        if delete_routine:
            self.routine_store.delete_managed(trigger.routine_id, self.MANAGED_BY)
        else:
            self.routine_store.detach_managed(trigger.routine_id, self.MANAGED_BY)
            self.routine_store.update(trigger.routine_id, group_id="")
        self._remove_empty_commands_group()
        self.save()
        return True

    def configure_default(self, default_id: str) -> TwitchCommandTrigger:
        existing = self.default(default_id)
        if existing is not None:
            return existing
        definition = self._default_definition(default_id)
        conflict = self._default_conflict(definition)
        if conflict:
            raise ValueError(conflict)
        return self._install_default(definition)

    def commit_social(
        self,
        information_store: ChannelInformationStore,
        service_id: str,
        url: str,
        include: bool,
    ) -> None:
        """Commit one row and its setup-driven defaults without publishing drafts.

        Prepare with the normal template factories. Keep configured routines and
        custom commands intact; an empty field disables its managed default.
        """
        if service_id not in SOCIAL_SERVICE_LABELS:
            raise ValueError("Unknown social service.")
        information = information_store.snapshot()
        information.social_links[service_id] = SocialLink(include, normalize_social_url(url))
        triggers = deepcopy(self.triggers)
        routines = deepcopy(self.routine_store.routines)
        groups = deepcopy(self.routine_store.groups)
        for definition in default_command_definitions():
            requirement = definition.setup_requirement
            if requirement not in {f"{service_id}_url", "socials"}:
                continue
            ready = (
                any(link.url and link.enabled_in_socials for link in information.social_links.values())
                if requirement == "socials"
                else bool(information.social_links[service_id].url)
            )
            trigger = next(
                (item for item in triggers if item.default_id == definition.default_id), None
            )
            if trigger is None and not ready:
                continue
            if trigger is None:
                # A custom command (including an alias) owns its name. Never
                # replace it just because a Channel Information field changes.
                occupant = self.resolve(definition.name)
                if occupant is not None:
                    continue
                conflict = self._default_conflict(definition)
                if conflict:
                    raise ValueError(conflict)
                group = next(
                    (item for item in groups
                     if item.name.casefold() == self.COMMANDS_GROUP_NAME.casefold()), None
                )
                if group is None:
                    group = RoutineGroup(uuid4().hex, self.COMMANDS_GROUP_NAME)
                    groups.append(group)
                trigger = self._trigger_for(definition)
                self._validate_trigger(trigger)
                routines.append(self._routine_for(definition, group_id=group.group_id))
                triggers.append(trigger)
            trigger.enabled = ready
        self.routine_store._validate_state(groups, routines)
        related_files = {}
        if groups != self.routine_store.groups or routines != self.routine_store.routines:
            related_files[self.routine_store.path] = {
                "version": self.routine_store.VERSION,
                "groups": [asdict(item) for item in groups],
                "routines": [asdict(item) for item in routines],
            }
        if triggers != self.triggers:
            related_files[self.path] = {
                "version": self.VERSION,
                "triggers": [asdict(item) for item in triggers],
            }
        information_store.save(information, related_files=related_files)
        self.triggers = triggers
        self.routine_store.groups = groups
        self.routine_store.routines = routines

    def reset_default(self, default_id: str) -> TwitchCommandTrigger:
        definition = self._default_definition(default_id)
        existing = self.default(default_id)
        if existing is None:
            raise ValueError("Configure the default command before resetting it.")
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
        group_id = self._ensure_commands_group()
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

    def _install_default(
        self, definition: DefaultCommandDefinition
    ) -> TwitchCommandTrigger:
        trigger = self._trigger_for(definition)
        routine = self._routine_for(
            definition,
            group_id=self._ensure_commands_group(),
        )
        self._validate_trigger(trigger)
        self.routine_store.routines.append(routine)
        self.triggers.append(trigger)
        try:
            self.routine_store.save()
            self.save()
        except Exception:
            self.routine_store.routines.remove(routine)
            self.triggers.remove(trigger)
            self._remove_empty_commands_group()
            raise
        return trigger

    def _remove_pristine_seeded_defaults(self) -> None:
        """Drop version-five startup defaults that were never used or edited."""
        definitions = {
            definition.default_id: definition
            for definition in default_command_definitions()
        }
        for trigger in tuple(self.triggers):
            definition = definitions.get(trigger.default_id)
            routine = self.routine_store.get(trigger.routine_id)
            expected = self._trigger_for(definition) if definition is not None else None
            pristine_trigger = bool(
                expected is not None
                and trigger.trigger_id == expected.trigger_id
                and trigger.routine_id == expected.routine_id
                and trigger.name == expected.name
                and trigger.aliases == expected.aliases
                and trigger.permission == expected.permission
                and trigger.global_cooldown_seconds == expected.global_cooldown_seconds
                and trigger.user_cooldown_seconds == expected.user_cooldown_seconds
                and trigger.enabled == expected.enabled
                and trigger.uses == 0
                and not trigger.last_used_at
                and trigger.has_chat_response == expected.has_chat_response
            )
            pristine_routine = bool(
                definition is not None
                and routine is not None
                and routine.routine_id == definition.routine_id
                and routine.name == f"Command !{definition.name}"
                and routine.trigger_id == definition.trigger_id
                and not routine.additional_trigger_ids
                and routine.managed_by == self.MANAGED_BY
                and routine.tasks == list(definition.tasks)
            )
            if pristine_trigger and pristine_routine:
                self.triggers.remove(trigger)
                self.routine_store.delete_managed(
                    trigger.routine_id, self.MANAGED_BY
                )

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
            group_id=self._ensure_commands_group(),
            task_type=SendTwitchChatMessageTask.task_type,
            task_name="Send Twitch chat response",
            task_config=(
                {"message": response, "as_bot": True}
                if response
                else None
            ),
        )

    def _ensure_commands_group(self) -> str:
        group = next(
            (
                value
                for value in self.routine_store.groups
                if value.name.casefold() == self.COMMANDS_GROUP_NAME.casefold()
            ),
            None,
        )
        if group is None:
            group = self.routine_store.add_group(self.COMMANDS_GROUP_NAME)
        return group.group_id

    def _remove_empty_commands_group(self) -> None:
        for group in tuple(self.routine_store.groups):
            if (
                group.name.casefold() == self.COMMANDS_GROUP_NAME.casefold()
                and not self.routine_store.grouped(group.group_id)
            ):
                self.routine_store.delete_group(group.group_id)

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
            generated_definitions: list[VariableDefinition] = []
            for candidate in routine.tasks:
                if candidate.task_id == task.task_id:
                    break
                generated_definitions.extend(
                    generated_output_definitions(
                        candidate.task_type,
                        candidate.config,
                    )
                )
            SendTwitchChatMessageTask.validate_template(
                str(task.config.get("message", "")),
                registry=self.variable_registry,
                extra_definitions=tuple(generated_definitions),
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
                "command_data": arguments,
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
