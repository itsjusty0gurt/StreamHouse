from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from products.hub.automation.models import RoutineDefinition
from products.hub.automation.routines import RoutineStore
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.core_triggers import CORE_TRIGGER_TYPES, CoreTriggerStore
from products.hub.obs_service.triggers import OBS_TRIGGER_TYPES, ObsTriggerStore
from products.hub.twitch.automation_triggers import (
    TWITCH_AUTOMATION_EVENT_TYPES,
    TwitchEventTriggerStore,
)
from products.hub.twitch.commands import TwitchCommandTriggerStore


FORMAT = "streamhouse.automation.routine"
VERSION = 1


def export_routine(
    routine: RoutineDefinition,
    *,
    routine_store: RoutineStore,
    command_store: TwitchCommandTriggerStore,
    event_store: TwitchEventTriggerStore,
    core_store: CoreTriggerStore,
    obs_store: ObsTriggerStore,
) -> dict[str, Any]:
    group = routine_store.get_group(routine.group_id)
    command = command_store.for_routine(routine.routine_id)
    command_payload: dict[str, Any] | None = None
    if command is not None:
        command_payload = {
            "name": command.name,
            "aliases": list(command.aliases),
            "permission": command.permission,
            "global_cooldown_seconds": command.global_cooldown_seconds,
            "user_cooldown_seconds": command.user_cooldown_seconds,
            "enabled": command.enabled,
            "response": command_store.response_for(command),
        }
    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "routine": {
            "name": routine.name,
            "description": routine.description,
            "enabled": routine.enabled,
            "group": group.name if group is not None else "",
            "tasks": [
                {
                    "task_type": task.task_type,
                    "name": task.name,
                    "config": task.config,
                    "enabled": task.enabled,
                }
                for task in routine.tasks
                if not task.managed_key
            ],
        },
        "triggers": {
            "twitch_command": command_payload,
            "twitch_events": [
                {
                    "event_type": trigger.event_type,
                    "filters": trigger.filters,
                    "enabled": trigger.enabled,
                }
                for trigger in event_store.for_routine(routine.routine_id)
            ],
            "core": [
                {"event_type": trigger.event_type, "enabled": trigger.enabled}
                for trigger in core_store.for_routine(routine.routine_id)
            ],
            "obs": [
                {
                    "event_type": trigger.event_type,
                    "filters": trigger.filters,
                    "enabled": trigger.enabled,
                }
                for trigger in obs_store.for_routine(routine.routine_id)
            ],
        },
    }


def validate_import(
    payload: Mapping[str, Any],
    *,
    task_registry: TaskRegistry,
    command_store: TwitchCommandTriggerStore,
) -> None:
    if (
        payload.get("format") != FORMAT
        or int(payload.get("version", 0)) != VERSION
    ):
        raise ValueError("This is not a supported Streamhouse routine file.")
    routine = payload.get("routine")
    triggers = payload.get("triggers", {})
    if not isinstance(routine, Mapping) or not isinstance(triggers, Mapping):
        raise ValueError("The routine file is missing its routine or trigger data.")
    if not str(routine.get("name", "")).strip():
        raise ValueError("The imported routine has no name.")
    tasks = routine.get("tasks", [])
    if not isinstance(tasks, list):
        raise ValueError("The imported task list is invalid.")
    available = set(task_registry.registered_types())
    unavailable: list[str] = []
    for task in tasks:
        if not isinstance(task, Mapping) or not isinstance(task.get("config", {}), Mapping):
            raise ValueError("The imported routine contains an invalid task.")
        task_type = str(task.get("task_type", "")).strip().casefold()
        if task_type not in available:
            unavailable.append(task_type or "missing task type")
    if unavailable:
        raise ValueError(
            "Unavailable task provider(s): " + ", ".join(sorted(set(unavailable)))
        )
    command = triggers.get("twitch_command")
    if command is not None:
        if not isinstance(command, Mapping):
            raise ValueError("The imported Twitch command is invalid.")
        names = [str(command.get("name", "")), *map(str, command.get("aliases", []))]
        for name in names:
            if command_store.resolve(name) is not None:
                raise ValueError(f"Twitch command already exists: !{name.lstrip('!')}")
    for key in ("twitch_events", "core", "obs"):
        if not isinstance(triggers.get(key, []), list):
            raise ValueError(f"The imported {key.replace('_', ' ')} trigger list is invalid.")
    supported = {
        "twitch_events": set(TWITCH_AUTOMATION_EVENT_TYPES),
        "core": set(CORE_TRIGGER_TYPES),
        "obs": set(OBS_TRIGGER_TYPES),
    }
    for key, event_types in supported.items():
        for trigger in triggers.get(key, []):
            if (
                not isinstance(trigger, Mapping)
                or str(trigger.get("event_type", "")) not in event_types
            ):
                raise ValueError(f"The imported routine contains an unsupported {key.replace('_', ' ')} trigger.")


def import_routine(
    payload: Mapping[str, Any],
    *,
    group_id: str,
    routine_store: RoutineStore,
    task_registry: TaskRegistry,
    command_store: TwitchCommandTriggerStore,
    event_store: TwitchEventTriggerStore,
    core_store: CoreTriggerStore,
    obs_store: ObsTriggerStore,
) -> RoutineDefinition:
    validate_import(
        payload,
        task_registry=task_registry,
        command_store=command_store,
    )
    routine_values = payload["routine"]
    trigger_values = payload["triggers"]
    name = _unique_routine_name(str(routine_values["name"]), routine_store.routines)
    routine = routine_store.add(
        name,
        group_id=group_id,
        description=str(routine_values.get("description", "")),
        enabled=bool(routine_values.get("enabled", True)),
    )
    event_ids: list[str] = []
    core_ids: list[str] = []
    obs_ids: list[str] = []
    command_id = ""
    try:
        for values in trigger_values.get("twitch_events", []):
            trigger = event_store.add(
                routine.routine_id,
                str(values.get("event_type", "")),
                filters=dict(values.get("filters", {})),
                enabled=bool(values.get("enabled", True)),
                reset_minutes=int(values.get("reset_minutes", 15)),
            )
            event_ids.append(trigger.trigger_id)
        for values in trigger_values.get("core", []):
            trigger = core_store.add(
                routine.routine_id,
                str(values.get("event_type", "")),
                enabled=bool(values.get("enabled", True)),
            )
            core_ids.append(trigger.trigger_id)
        for values in trigger_values.get("obs", []):
            trigger = obs_store.add(
                routine.routine_id,
                str(values.get("event_type", "")),
                filters=dict(values.get("filters", {})),
                enabled=bool(values.get("enabled", True)),
            )
            obs_ids.append(trigger.trigger_id)
        command = trigger_values.get("twitch_command")
        if isinstance(command, Mapping):
            created = command_store.attach_routine(
                routine.routine_id,
                str(command.get("name", "")),
                str(command.get("response", "")),
                aliases=[str(value) for value in command.get("aliases", [])],
                permission=str(command.get("permission", "everyone")),
                global_cooldown_seconds=int(command.get("global_cooldown_seconds", 10)),
                user_cooldown_seconds=int(command.get("user_cooldown_seconds", 30)),
            )
            command_id = created.trigger_id
            command_store.set_enabled(command_id, bool(command.get("enabled", True)))
        for values in routine_values.get("tasks", []):
            routine_store.add_task(
                routine.routine_id,
                task_type=str(values.get("task_type", "")),
                name=str(values.get("name", "Imported task")),
                config=dict(values.get("config", {})),
                enabled=bool(values.get("enabled", True)),
            )
    except (OSError, TypeError, ValueError):
        if command_id:
            command_store.delete(command_id, delete_routine=False)
        for trigger_id in reversed(obs_ids):
            obs_store.delete(trigger_id)
        for trigger_id in reversed(core_ids):
            core_store.delete(trigger_id)
        for trigger_id in reversed(event_ids):
            event_store.delete(trigger_id)
        routine_store.delete(routine.routine_id, allow_managed=True)
        raise
    return routine_store.get(routine.routine_id)  # type: ignore[return-value]


def _unique_routine_name(name: str, routines: Iterable[RoutineDefinition]) -> str:
    clean = name.strip()
    occupied = {routine.name.casefold() for routine in routines}
    if clean.casefold() not in occupied:
        return clean
    candidate = f"{clean} (Imported)"
    suffix = 2
    while candidate.casefold() in occupied:
        candidate = f"{clean} (Imported {suffix})"
        suffix += 1
    return candidate
