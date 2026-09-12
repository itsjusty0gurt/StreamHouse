from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterable, Mapping
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from products.hub.automation.core_triggers import CoreTriggerStore
from products.hub.automation.custom_variables import CustomVariableStore
from products.hub.automation.queues import AutomationQueueStore
from products.hub.automation.routines import RoutineStore
from products.hub.automation.models import DEFAULT_AUTOMATION_QUEUE_ID
from products.hub.core.settings import SettingsStore
from products.hub.counters.models import CounterDefinition
from products.hub.counters.store import COUNTER_VERSION, INDEX_VERSION, CounterStore
from products.hub.obs_service.triggers import ObsTriggerStore
from products.hub.twitch.automation_triggers import TwitchEventTriggerStore
from products.hub.twitch.channel_information import ChannelInformationStore
from products.hub.twitch.chatter_history import ChatterHistoryStore
from products.hub.twitch.commands import TwitchCommandTriggerStore
from shared.streamhouse_runtime.paths import user_data_root
from shared.streamhouse_runtime.version import VERSION


class BackupError(ValueError):
    """A backup is unsafe, incomplete, corrupt, or incompatible."""


class BackupComponent(str, Enum):
    ROUTINES = "routines"
    COMMANDS = "commands"
    COUNTER_DEFINITIONS = "counter_definitions"
    COUNTER_VALUES = "counter_values"
    CUSTOM_VARIABLES = "custom_variables"
    CHANNEL_INFORMATION = "channel_information"
    USERS = "users"
    HUB_SETTINGS = "hub_settings"
    OBS_CONFIGURATION = "obs_configuration"


class BackupPreset(str, Enum):
    RECOMMENDED = "recommended"
    CONFIGURATION_ONLY = "configuration_only"
    EVERYTHING_ELIGIBLE = "everything_eligible"
    CUSTOM = "custom"


CONFIGURATION_COMPONENTS = frozenset(
    {
        BackupComponent.ROUTINES,
        BackupComponent.COMMANDS,
        BackupComponent.COUNTER_DEFINITIONS,
        BackupComponent.CUSTOM_VARIABLES,
        BackupComponent.CHANNEL_INFORMATION,
        BackupComponent.HUB_SETTINGS,
        BackupComponent.OBS_CONFIGURATION,
    }
)
PRESET_COMPONENTS: Mapping[BackupPreset, frozenset[BackupComponent]] = {
    BackupPreset.CONFIGURATION_ONLY: CONFIGURATION_COMPONENTS,
    BackupPreset.RECOMMENDED: CONFIGURATION_COMPONENTS
    | {BackupComponent.COUNTER_VALUES},
    BackupPreset.EVERYTHING_ELIGIBLE: frozenset(BackupComponent),
}
COMPONENT_LABELS: Mapping[BackupComponent, str] = {
    BackupComponent.ROUTINES: "Routines & Dependencies",
    BackupComponent.COMMANDS: "Commands",
    BackupComponent.COUNTER_DEFINITIONS: "Counter Definitions / Settings",
    BackupComponent.COUNTER_VALUES: "Counter Values",
    BackupComponent.CUSTOM_VARIABLES: "Custom Variables",
    BackupComponent.CHANNEL_INFORMATION: "Channel Information",
    BackupComponent.USERS: "Users / Chatter Management Data",
    BackupComponent.HUB_SETTINGS: "Hub Settings",
    BackupComponent.OBS_CONFIGURATION: "OBS Hub Configuration",
}
_COMPONENT_SCHEMAS: Mapping[BackupComponent, int] = {
    BackupComponent.ROUTINES: 1,
    BackupComponent.COMMANDS: TwitchCommandTriggerStore.VERSION,
    BackupComponent.COUNTER_DEFINITIONS: INDEX_VERSION,
    BackupComponent.COUNTER_VALUES: COUNTER_VERSION,
    BackupComponent.CUSTOM_VARIABLES: CustomVariableStore.VERSION,
    BackupComponent.CHANNEL_INFORMATION: ChannelInformationStore.VERSION,
    BackupComponent.USERS: ChatterHistoryStore.VERSION,
    BackupComponent.HUB_SETTINGS: SettingsStore.VERSION,
    BackupComponent.OBS_CONFIGURATION: 1,
}
_DEPENDENCIES: Mapping[BackupComponent, frozenset[BackupComponent]] = {
    BackupComponent.COUNTER_VALUES: frozenset(
        {BackupComponent.COUNTER_DEFINITIONS}
    ),
}
_PORTABLE_SETTINGS = frozenset(
    {
        "startup_page",
        "log_level",
        "ui_log_limit",
        "show_developer_tools",
        "twitch_chat_show_timestamps",
        "twitch_chat_font_family",
        "twitch_chat_font_size",
        "twitch_last_ad_duration",
        "automatic_backups_enabled",
    }
)
_USER_MANAGEMENT_FIELDS = frozenset(
    {
        "user_id",
        "user_name",
        "user_login",
        "first_seen",
        "last_seen",
        "active_days",
        "message_count",
        "snapshot_days",
        "last_snapshot_day",
        "is_bot",
        "roles",
        "followed_at",
        "manual_group",
        "twitch_status",
    }
)
_SECRET_KEYS = re.compile(
    r"(?i)(?:authorization|access[_-]?token|refresh[_-]?token|api[_-]?key|"
    r"client[_-]?secret|password|cookie|session[_-]?secret|relay[_-]?(?:key|secret)|"
    r"x-sally-[\w-]+|sally_relay_(?:base|keys|db))"
)
_SECRET_TEXT = re.compile(
    r"(?i)(?:authorization\s*[:=]\s*(?:bearer|oauth)\s+\S+|"
    r"(?:access_token|refresh_token|api_key|client_secret|password|secret)"
    r"\s*[:=]\s*\S+|"
    r"[?&](?:access_token|refresh_token|api_key|client_secret|token|key|secret|password)=)"
)
_CUSTOM_PLACEHOLDER = re.compile(
    r"\{custom\.([a-z][a-z0-9_]{0,63})(?:[^}]*)\}"
)


@dataclass(frozen=True, slots=True)
class BackupInspection:
    archive: Path
    created_at: str
    hub_version: str
    backup_type: str
    preset: str
    components: tuple[BackupComponent, ...]
    schemas: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class RestoreReport:
    archive: Path
    restored_components: tuple[str, ...]
    safety_backup: Path
    restart_required: bool = True

    @property
    def restored_files(self) -> tuple[str, ...]:
        return self.restored_components


@dataclass(frozen=True, slots=True)
class BackupSummary:
    components: tuple[BackupComponent, ...]
    counts: Mapping[str, int]


class BackupManager:
    """Create and restore versioned, selective Hub configuration archives."""

    FORMAT_VERSION = 1
    EXTENSION = ".streamhousebackup"
    AUTOMATIC_RETENTION = 5

    def __init__(
        self,
        project_root: Path | None = None,
        backup_directory: Path | None = None,
        *,
        now=None,
    ) -> None:
        self.project_root = Path(project_root or user_data_root())
        self.backup_directory = Path(
            backup_directory or self.project_root / "backups"
        )
        self.manual_directory = self.backup_directory / "manual"
        self.automatic_directory = self.backup_directory / "automatic"
        self.safety_directory = self.backup_directory / "safety"
        self._now = now or (lambda: datetime.now(timezone.utc))

    @classmethod
    def components_for(
        cls,
        preset: BackupPreset | str,
        selected: Iterable[BackupComponent | str] = (),
    ) -> frozenset[BackupComponent]:
        preset = BackupPreset(preset)
        if preset is BackupPreset.CUSTOM:
            values = {BackupComponent(value) for value in selected}
            if not values:
                raise BackupError("Select at least one backup component.")
        else:
            values = set(PRESET_COMPONENTS[preset])
        changed = True
        while changed:
            changed = False
            for component in tuple(values):
                for dependency in _DEPENDENCIES.get(component, ()):
                    if dependency not in values:
                        values.add(dependency)
                        changed = True
        return frozenset(values)

    def summarize(
        self,
        preset: BackupPreset | str = BackupPreset.RECOMMENDED,
        selected: Iterable[BackupComponent | str] = (),
    ) -> BackupSummary:
        components = self.components_for(preset, selected)
        payloads = self._snapshot(components)
        counts: dict[str, int] = {}
        routines = payloads.get(BackupComponent.ROUTINES, {})
        if isinstance(routines, dict):
            counts["routines"] = len(
                routines.get("routines", {}).get("routines", [])
            )
            counts["triggers"] = sum(
                len(store.get("triggers", []))
                for store in routines.get("triggers", {}).values()
                if isinstance(store, dict)
            )
            counts["queues"] = len(routines.get("queues", {}).get("queues", []))
        commands = payloads.get(BackupComponent.COMMANDS, {})
        if isinstance(commands, dict):
            counts["commands"] = len(commands.get("triggers", []))
        definitions = payloads.get(BackupComponent.COUNTER_DEFINITIONS, {})
        if isinstance(definitions, dict):
            counts["counters"] = len(definitions.get("counters", []))
        variables = payloads.get(BackupComponent.CUSTOM_VARIABLES, {})
        if isinstance(variables, dict):
            counts["custom_variables"] = len(variables.get("global", {}))
        users = payloads.get(BackupComponent.USERS, {})
        if isinstance(users, dict):
            counts["users"] = len(users.get("chatters", {}))
        return BackupSummary(
            tuple(sorted(components, key=lambda item: item.value)), counts
        )

    def create(
        self,
        label: str = "manual",
        *,
        preset: BackupPreset | str = BackupPreset.RECOMMENDED,
        components: Iterable[BackupComponent | str] = (),
        destination: Path | None = None,
    ) -> Path:
        backup_type = self._normalize_type(label)
        selected = self.components_for(preset, components)
        payloads = self._snapshot(selected)
        encoded = {
            component: self._encode(payload)
            for component, payload in payloads.items()
        }
        for component, content in encoded.items():
            self._assert_safe_payload(component, json.loads(content))
        created_at = self._now().astimezone(timezone.utc)
        component_manifest = {
            component.value: {
                "schema": _COMPONENT_SCHEMAS[component],
                "path": f"components/{component.value}.json",
                "sha256": hashlib.sha256(content).hexdigest(),
                "dependencies": [
                    dependency.value
                    for dependency in sorted(
                        _DEPENDENCIES.get(component, ()),
                        key=lambda item: item.value,
                    )
                    if dependency in selected
                ],
                **(
                    {
                        "source_schemas": {
                            "routines": RoutineStore.VERSION,
                            "commands": TwitchCommandTriggerStore.VERSION,
                            "twitch_triggers": TwitchEventTriggerStore.VERSION,
                            "core_triggers": CoreTriggerStore.VERSION,
                            "obs_triggers": ObsTriggerStore.VERSION,
                            "queues": AutomationQueueStore.VERSION,
                            "custom_variables": CustomVariableStore.VERSION,
                            "counter_definitions": INDEX_VERSION,
                        }
                    }
                    if component is BackupComponent.ROUTINES
                    else {}
                ),
            }
            for component, content in sorted(
                encoded.items(), key=lambda item: item[0].value
            )
        }
        content_digest = hashlib.sha256(
            b"".join(
                component.value.encode("utf-8") + encoded[component]
                for component in sorted(encoded, key=lambda item: item.value)
            )
        ).hexdigest()
        manifest = {
            "backup_format_version": self.FORMAT_VERSION,
            "product": "Streamhouse Hub",
            "hub_version": VERSION,
            "build": (
                "Packaged Windows build"
                if getattr(sys, "frozen", False)
                else "Development build"
            ),
            "created_at": created_at.isoformat(),
            "backup_type": backup_type,
            "preset": BackupPreset(preset).value,
            "included_components": [
                item.value for item in sorted(selected, key=lambda item: item.value)
            ],
            "components": component_manifest,
            "content_digest": content_digest,
        }
        target_directory = self._directory_for(backup_type)
        target_directory.mkdir(parents=True, exist_ok=True)
        if destination is None:
            stamp = created_at.strftime("%Y-%m-%d-%H%M%S-%f")
            destination = target_directory / (
                f"StreamhouseHub-Backup-{stamp}{self.EXTENSION}"
            )
        destination = Path(destination)
        if destination.suffix.casefold() != self.EXTENSION:
            destination = destination.with_suffix(self.EXTENSION)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        try:
            with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", self._encode(manifest))
                for component, content in encoded.items():
                    archive.writestr(
                        f"components/{component.value}.json", content
                    )
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        if backup_type == "automatic":
            self._rotate_automatic()
        return destination

    def create_daily_if_needed(self) -> Path | None:
        components = self.components_for(BackupPreset.RECOMMENDED)
        payloads = self._snapshot(components)
        digest = hashlib.sha256(
            b"".join(
                component.value.encode("utf-8") + self._encode(payloads[component])
                for component in sorted(payloads, key=lambda item: item.value)
            )
        ).hexdigest()
        latest = self.latest("automatic")
        if latest is not None:
            try:
                with ZipFile(latest) as archive:
                    manifest = json.loads(archive.read("manifest.json"))
                created = datetime.fromisoformat(str(manifest["created_at"]))
                if (
                    created.astimezone(timezone.utc).date()
                    == self._now().astimezone(timezone.utc).date()
                    or manifest.get("content_digest") == digest
                ):
                    return None
            except (BadZipFile, KeyError, ValueError, json.JSONDecodeError):
                pass
        return self.create("automatic", preset=BackupPreset.RECOMMENDED)

    def latest(self, backup_type: str | None = None) -> Path | None:
        directories = (
            (self._directory_for(backup_type),)
            if backup_type is not None
            else (
                self.manual_directory,
                self.automatic_directory,
                self.safety_directory,
            )
        )
        archives = [
            path
            for directory in directories
            for path in directory.glob(f"*{self.EXTENSION}")
        ]
        return max(archives, key=lambda path: path.stat().st_mtime, default=None)

    def inspect(self, archive: Path) -> BackupInspection:
        manifest, _payloads = self._read_archive(Path(archive))
        components = tuple(BackupComponent(name) for name in manifest["components"])
        return BackupInspection(
            Path(archive),
            str(manifest["created_at"]),
            str(manifest["hub_version"]),
            str(manifest["backup_type"]),
            str(manifest["preset"]),
            components,
            {
                name: int(details["schema"])
                for name, details in manifest["components"].items()
            },
        )

    def restore(
        self,
        archive: Path,
        components: Iterable[BackupComponent | str] | None = None,
        *,
        create_safety: bool = True,
        active_stream_id: str = "",
    ) -> RestoreReport:
        archive = Path(archive)
        manifest, payloads = self._read_archive(archive)
        available = {BackupComponent(name) for name in manifest["components"]}
        selected = (
            available
            if components is None
            else {BackupComponent(value) for value in components}
        )
        if not selected or not selected.issubset(available):
            raise BackupError("Choose components that exist in this backup.")
        selected = self._expand_restore_dependencies(selected, manifest)
        writes, deletes = self._restore_plan(
            selected,
            payloads,
            active_stream_id=str(active_stream_id).strip(),
        )
        self._validate_staged(writes)
        if create_safety:
            try:
                safety = self.create(
                    "safety", preset=BackupPreset.EVERYTHING_ELIGIBLE
                )
            except Exception as error:
                raise BackupError(
                    "Restore stopped because the safety backup failed."
                ) from error
        else:
            safety = Path()
        self._commit_transaction(writes, deletes)
        return RestoreReport(
            archive,
            tuple(
                item.value for item in sorted(selected, key=lambda item: item.value)
            ),
            safety,
        )

    def scrub_viewer(self, user_id: str) -> int:
        clean_id = user_id.strip()
        if not clean_id:
            return 0
        changed = 0
        directories = (
            self.manual_directory,
            self.automatic_directory,
            self.safety_directory,
        )
        for archive in (
            path
            for directory in directories
            for path in directory.glob(f"*{self.EXTENSION}")
        ):
            try:
                manifest, payloads = self._read_archive(archive)
            except BackupError:
                continue
            archive_changed = False
            users = payloads.get(BackupComponent.USERS)
            if isinstance(users, dict):
                chatters = users.get("chatters", {})
                if (
                    isinstance(chatters, dict)
                    and chatters.pop(clean_id, None) is not None
                ):
                    archive_changed = True
            counter_values = payloads.get(BackupComponent.COUNTER_VALUES)
            if isinstance(counter_values, dict):
                values = counter_values.get("values", {})
                if isinstance(values, dict):
                    for counter in values.values():
                        if not isinstance(counter, dict):
                            continue
                        viewers = counter.get("viewers", {})
                        if (
                            isinstance(viewers, dict)
                            and viewers.pop(clean_id, None) is not None
                        ):
                            archive_changed = True
            if archive_changed:
                self._rewrite_archive(archive, manifest, payloads)
                changed += 1
        return changed

    # Snapshot ---------------------------------------------------------

    def _snapshot(
        self, components: frozenset[BackupComponent]
    ) -> dict[BackupComponent, Any]:
        payloads: dict[BackupComponent, Any] = {}
        if BackupComponent.ROUTINES in components:
            payloads[BackupComponent.ROUTINES] = self._routine_snapshot()
        if BackupComponent.COMMANDS in components:
            payloads[BackupComponent.COMMANDS] = self._command_snapshot(
                routines_already_included=BackupComponent.ROUTINES in components
            )
        if BackupComponent.COUNTER_DEFINITIONS in components:
            payloads[BackupComponent.COUNTER_DEFINITIONS] = (
                self._counter_definitions()
            )
        if BackupComponent.COUNTER_VALUES in components:
            payloads[BackupComponent.COUNTER_VALUES] = self._counter_values()
        if BackupComponent.CUSTOM_VARIABLES in components:
            payloads[BackupComponent.CUSTOM_VARIABLES] = self._json_or_default(
                "automation/variables.json",
                {
                    "version": CustomVariableStore.VERSION,
                    "global": {},
                    "metadata": {},
                },
            )
        if BackupComponent.CHANNEL_INFORMATION in components:
            payloads[BackupComponent.CHANNEL_INFORMATION] = self._json_or_default(
                "twitch/channel-information.json",
                {
                    "version": ChannelInformationStore.VERSION,
                    "social_links": {},
                    "schedule": "",
                    "rules": "",
                    "server_info": "",
                },
            )
        if BackupComponent.USERS in components:
            payloads[BackupComponent.USERS] = self._safe_users_snapshot()
        if BackupComponent.HUB_SETTINGS in components:
            payloads[BackupComponent.HUB_SETTINGS] = self._safe_settings_snapshot()
        if BackupComponent.OBS_CONFIGURATION in components:
            payloads[BackupComponent.OBS_CONFIGURATION] = self._safe_obs_snapshot()
        return payloads

    def _routine_snapshot(
        self,
        routine_ids: Iterable[str] | None = None,
        *,
        scope: str = "all",
    ) -> dict[str, Any]:
        raw = self._json_or_default(
            "automation/routines.json",
            {"version": RoutineStore.VERSION, "groups": [], "routines": []},
        )
        self._expect_schema(raw, RoutineStore.VERSION, "Routines", "version")
        all_routines = {
            str(item.get("routine_id", "")): item
            for item in raw.get("routines", [])
            if isinstance(item, dict) and str(item.get("routine_id", ""))
        }
        wanted = (
            set(all_routines)
            if routine_ids is None
            else {str(value) for value in routine_ids if str(value)}
        )
        pending = list(wanted)
        custom_names: set[str] = set()
        queue_ids: set[str] = {DEFAULT_AUTOMATION_QUEUE_ID}
        counter_ids: set[str] = set()
        while pending:
            routine_id = pending.pop()
            routine = all_routines.get(routine_id)
            if routine is None:
                raise BackupError(f'Routine dependency "{routine_id}" is missing.')
            nested, task_queues, task_counters, task_custom = (
                self._task_dependencies(routine.get("tasks", []))
            )
            queue_ids.add(
                str(
                    routine.get("queue_id", DEFAULT_AUTOMATION_QUEUE_ID)
                )
                or DEFAULT_AUTOMATION_QUEUE_ID
            )
            queue_ids.update(task_queues)
            counter_ids.update(task_counters)
            custom_names.update(task_custom)
            for nested_id in nested:
                if nested_id not in wanted:
                    wanted.add(nested_id)
                    pending.append(nested_id)
        routines = [
            item
            for item in raw.get("routines", [])
            if item.get("routine_id") in wanted
        ]
        group_ids = {
            str(item.get("group_id", ""))
            for item in routines
            if item.get("group_id")
        }
        groups = [
            item
            for item in raw.get("groups", [])
            if item.get("group_id") in group_ids
        ]
        trigger_ids = {
            str(trigger_id)
            for routine in routines
            for trigger_id in (
                routine.get("trigger_id", ""),
                *routine.get("additional_trigger_ids", []),
            )
            if str(trigger_id)
        }
        triggers: dict[str, Any] = {}
        found_trigger_ids: set[str] = set()
        trigger_sources = (
            (
                "commands",
                "twitch/commands.json",
                TwitchCommandTriggerStore.VERSION,
            ),
            (
                "twitch",
                "twitch/event_triggers.json",
                TwitchEventTriggerStore.VERSION,
            ),
            (
                "core",
                "automation/core_triggers.json",
                CoreTriggerStore.VERSION,
            ),
            ("obs", "obs/triggers.json", ObsTriggerStore.VERSION),
        )
        for label, relative, version in trigger_sources:
            store = self._json_or_default(
                relative, {"version": version, "triggers": []}
            )
            self._expect_schema(store, version, f"{label} triggers", "version")
            selected = [
                item
                for item in store.get("triggers", [])
                if isinstance(item, dict)
                and str(item.get("trigger_id", "")) in trigger_ids
                and str(item.get("routine_id", "")) in wanted
            ]
            found_trigger_ids.update(
                str(item.get("trigger_id", "")) for item in selected
            )
            triggers[label] = {"version": version, "triggers": selected}
        missing = trigger_ids - found_trigger_ids
        if missing:
            raise BackupError(
                "Routine backup stopped because trigger dependencies are missing: "
                + ", ".join(sorted(missing))
            )
        queues = self._json_or_default(
            "automation/queues.json",
            {
                "version": AutomationQueueStore.VERSION,
                "queues": [
                    {
                        "queue_id": DEFAULT_AUTOMATION_QUEUE_ID,
                        "name": "Default Queue",
                        "paused": False,
                        "max_length": 100,
                        "duplicate_policy": "allow",
                        "delay_seconds": 0.0,
                    }
                ],
            },
        )
        self._expect_schema(
            queues, AutomationQueueStore.VERSION, "Queues", "version"
        )
        selected_queues = [
            item
            for item in queues.get("queues", [])
            if isinstance(item, dict)
            and str(item.get("queue_id", "")) in queue_ids
        ]
        found_queues = {str(item.get("queue_id", "")) for item in selected_queues}
        if queue_ids - found_queues:
            raise BackupError(
                "Routine backup stopped because queue dependencies are missing: "
                + ", ".join(sorted(queue_ids - found_queues))
            )
        variables = self._json_or_default(
            "automation/variables.json",
            {
                "version": CustomVariableStore.VERSION,
                "global": {},
                "metadata": {},
            },
        )
        self._expect_schema(
            variables,
            CustomVariableStore.VERSION,
            "Custom Variables",
            "version",
        )
        global_values = variables.get("global", {})
        if not isinstance(global_values, dict):
            raise BackupError("Custom Variable data is invalid.")
        missing_custom = custom_names - set(global_values)
        if missing_custom:
            raise BackupError(
                "Routine backup stopped because custom Variable dependencies are missing: "
                + ", ".join(
                    f"custom.{name}" for name in sorted(missing_custom)
                )
            )
        definitions = self._counter_definitions()
        by_counter = {
            str(item.get("counter_id", "")): item
            for item in definitions.get("counters", [])
            if isinstance(item, dict)
        }
        missing_counters = counter_ids - set(by_counter)
        if missing_counters:
            raise BackupError(
                "Routine backup stopped because Counter dependencies are missing: "
                + ", ".join(sorted(missing_counters))
            )
        metadata = variables.get("metadata", {})
        return {
            "schema": _COMPONENT_SCHEMAS[BackupComponent.ROUTINES],
            "scope": scope,
            "routines": {
                "version": RoutineStore.VERSION,
                "groups": groups,
                "routines": routines,
            },
            "triggers": triggers,
            "queues": {
                "version": AutomationQueueStore.VERSION,
                "queues": selected_queues,
            },
            "custom_variables": {
                "version": CustomVariableStore.VERSION,
                "global": {
                    name: global_values[name] for name in sorted(custom_names)
                },
                "metadata": {
                    name: metadata.get(name, {})
                    for name in sorted(custom_names)
                    if isinstance(metadata, dict) and name in metadata
                },
            },
            "counter_definitions": {
                "version": INDEX_VERSION,
                "counters": [by_counter[name] for name in sorted(counter_ids)],
            },
        }

    def _command_snapshot(self, *, routines_already_included: bool) -> dict[str, Any]:
        commands = self._json_or_default(
            "twitch/commands.json",
            {"version": TwitchCommandTriggerStore.VERSION, "triggers": []},
        )
        self._expect_schema(
            commands,
            TwitchCommandTriggerStore.VERSION,
            "Commands",
            "version",
        )
        payload: dict[str, Any] = dict(commands)
        payload["schema"] = TwitchCommandTriggerStore.VERSION
        if not routines_already_included:
            payload["routine_dependencies"] = self._routine_snapshot(
                (
                    item.get("routine_id", "")
                    for item in commands.get("triggers", [])
                    if isinstance(item, dict)
                ),
                scope="commands",
            )
        return payload

    def _counter_definitions(self) -> dict[str, Any]:
        payload = self._json_or_default(
            "counters/index.json", {"version": INDEX_VERSION, "counters": []}
        )
        self._expect_schema(
            payload, INDEX_VERSION, "Counter definitions", "version"
        )
        values = payload.get("counters", [])
        if not isinstance(values, list):
            raise BackupError("Counter definitions contain invalid data.")
        try:
            for value in values:
                if not isinstance(value, dict):
                    raise ValueError
                CounterDefinition.from_dict(value)
        except (TypeError, ValueError) as error:
            raise BackupError("Counter definitions contain invalid data.") from error
        return payload

    def _counter_values(self) -> dict[str, Any]:
        definitions = self._counter_definitions()
        store = CounterStore(self.project_root / "counters")
        values = {
            str(item["counter_id"]): store.read_data(str(item["counter_id"]))
            for item in definitions.get("counters", [])
        }
        return {"schema": COUNTER_VERSION, "values": values}

    def _safe_users_snapshot(self) -> dict[str, Any]:
        payload = self._json_or_default(
            "memory/twitch_chatters.json",
            {"version": ChatterHistoryStore.VERSION, "chatters": {}},
        )
        self._expect_schema(
            payload, ChatterHistoryStore.VERSION, "Users", "version"
        )
        records = payload.get("chatters", {})
        if not isinstance(records, dict):
            raise BackupError("Users data is invalid.")
        safe = {
            str(user_id): {
                key: value
                for key, value in record.items()
                if key in _USER_MANAGEMENT_FIELDS
            }
            for user_id, record in records.items()
            if str(user_id).strip() and isinstance(record, dict)
        }
        return {"version": ChatterHistoryStore.VERSION, "chatters": safe}

    def _safe_settings_snapshot(self) -> dict[str, Any]:
        payload = self._json_or_default(
            "config/settings.json", {"_version": SettingsStore.VERSION}
        )
        self._expect_schema(
            payload, SettingsStore.VERSION, "Hub settings", "_version"
        )
        return {
            "_version": SettingsStore.VERSION,
            **{key: payload[key] for key in _PORTABLE_SETTINGS if key in payload},
        }

    def _safe_obs_snapshot(self) -> dict[str, Any]:
        payload = self._json_or_default(
            "obs/connection.json",
            {
                "version": 1,
                "host": "127.0.0.1",
                "port": 4455,
                "auto_connect": False,
                "default_mute_input": "",
            },
        )
        self._expect_schema(payload, 1, "OBS configuration", "version")
        return {
            "version": 1,
            **{
                key: payload[key]
                for key in (
                    "host",
                    "port",
                    "auto_connect",
                    "default_mute_input",
                )
                if key in payload
            },
        }

    # Restore ----------------------------------------------------------

    def _restore_plan(
        self,
        selected: set[BackupComponent],
        payloads: Mapping[BackupComponent, Any],
        *,
        active_stream_id: str = "",
    ) -> tuple[dict[Path, bytes], set[Path]]:
        writes: dict[Path, bytes] = {}
        deletes: set[Path] = set()
        if BackupComponent.ROUTINES in selected:
            self._apply_routine_restore(payloads[BackupComponent.ROUTINES], writes)
        if BackupComponent.COMMANDS in selected:
            command_payload = dict(payloads[BackupComponent.COMMANDS])
            dependencies = command_payload.pop("routine_dependencies", None)
            command_payload.pop("schema", None)
            if dependencies is not None and BackupComponent.ROUTINES not in selected:
                self._apply_routine_restore(dependencies, writes)
            writes[self.project_root / "twitch/commands.json"] = self._encode(
                command_payload
            )
        if BackupComponent.COUNTER_DEFINITIONS in selected:
            writes[self.project_root / "counters/index.json"] = self._encode(
                payloads[BackupComponent.COUNTER_DEFINITIONS]
            )
        if BackupComponent.COUNTER_VALUES in selected:
            counter_payload = payloads[BackupComponent.COUNTER_VALUES]
            values = counter_payload.get("values", {})
            if not isinstance(values, dict):
                raise BackupError("Counter Values are invalid.")
            definitions = payloads.get(BackupComponent.COUNTER_DEFINITIONS, {})
            reset_values = {
                str(item.get("counter_id", "")): str(
                    item.get("reset_value", "0")
                )
                for item in definitions.get("counters", [])
                if isinstance(item, dict)
            }
            counter_directory = self.project_root / "counters"
            existing_ids = {
                path.stem
                for path in counter_directory.glob("*.json")
                if path.name != "index.json"
            }
            for counter_id, value in values.items():
                target = counter_directory / f"{counter_id}.json"
                writes[target] = self._encode(
                    self._safe_stream_restore(
                        target,
                        value,
                        active_stream_id=active_stream_id,
                        reset_value=reset_values.get(str(counter_id), "0"),
                    )
                )
            deletes.update(
                counter_directory / f"{counter_id}.json"
                for counter_id in existing_ids - set(values)
            )
        direct = {
            BackupComponent.CUSTOM_VARIABLES: "automation/variables.json",
            BackupComponent.CHANNEL_INFORMATION: "twitch/channel-information.json",
            BackupComponent.USERS: "memory/twitch_chatters.json",
            BackupComponent.HUB_SETTINGS: "config/settings.json",
            BackupComponent.OBS_CONFIGURATION: "obs/connection.json",
        }
        for component, relative in direct.items():
            if component in selected:
                writes[self.project_root / relative] = self._encode(
                    payloads[component]
                )
        return writes, deletes

    def _apply_routine_restore(
        self,
        payload: Mapping[str, Any],
        writes: dict[Path, bytes],
    ) -> None:
        scope = str(payload.get("scope", "all"))
        routine_payload = payload["routines"]
        restored_routine_ids = {
            str(item.get("routine_id", ""))
            for item in routine_payload.get("routines", [])
            if isinstance(item, dict)
        }
        if scope == "commands":
            current = self._json_or_default(
                "automation/routines.json",
                {"version": RoutineStore.VERSION, "groups": [], "routines": []},
            )
            kept = [
                item
                for item in current.get("routines", [])
                if not isinstance(item, dict)
                or item.get("managed_by") != TwitchCommandTriggerStore.MANAGED_BY
            ]
            incoming = list(routine_payload.get("routines", []))
            group_ids = {
                str(item.get("group_id", ""))
                for item in [*kept, *incoming]
                if isinstance(item, dict) and item.get("group_id")
            }
            groups_by_id = {
                str(item.get("group_id")): item
                for item in [
                    *current.get("groups", []),
                    *routine_payload.get("groups", []),
                ]
                if isinstance(item, dict)
            }
            routine_payload = {
                "version": RoutineStore.VERSION,
                "groups": [
                    groups_by_id[group_id]
                    for group_id in group_ids
                    if group_id in groups_by_id
                ],
                "routines": [*kept, *incoming],
            }
        writes[self.project_root / "automation/routines.json"] = self._encode(
            routine_payload
        )
        trigger_paths = {
            "commands": "twitch/commands.json",
            "twitch": "twitch/event_triggers.json",
            "core": "automation/core_triggers.json",
            "obs": "obs/triggers.json",
        }
        for name, store in payload.get("triggers", {}).items():
            if name in trigger_paths:
                target = self.project_root / trigger_paths[name]
                if scope == "commands":
                    current = self._read_path_json(
                        target,
                        {
                            "version": store.get("version"),
                            "triggers": [],
                        },
                    )
                    retained = [
                        item
                        for item in current.get("triggers", [])
                        if not isinstance(item, dict)
                        or str(item.get("routine_id", ""))
                        not in restored_routine_ids
                    ]
                    store = {
                        **current,
                        "version": store.get("version"),
                        "triggers": [
                            *retained,
                            *store.get("triggers", []),
                        ],
                    }
                writes[target] = self._encode(store)
        self._merge_dependency_store(
            self.project_root / "automation/queues.json",
            payload.get("queues", {}),
            "queues",
            "queue_id",
            writes,
        )
        self._merge_custom_variables(payload.get("custom_variables", {}), writes)
        self._merge_counter_definitions(
            payload.get("counter_definitions", {}), writes
        )

    def _merge_dependency_store(
        self,
        target: Path,
        incoming: Mapping[str, Any],
        collection: str,
        id_key: str,
        writes: dict[Path, bytes],
    ) -> None:
        current = (
            json.loads(writes[target])
            if target in writes
            else self._read_path_json(
                target,
                {"version": incoming.get("version"), collection: []},
            )
        )
        by_id = {
            str(item.get(id_key)): item
            for item in current.get(collection, [])
            if isinstance(item, dict)
        }
        for item in incoming.get(collection, []):
            if isinstance(item, dict):
                by_id[str(item.get(id_key))] = item
        ordered = list(by_id.values())
        if collection == "queues":
            ordered.sort(
                key=lambda item: (
                    item.get("queue_id") != DEFAULT_AUTOMATION_QUEUE_ID,
                    str(item.get("name", "")).casefold(),
                )
            )
        writes[target] = self._encode(
            {"version": incoming.get("version"), collection: ordered}
        )

    def _merge_custom_variables(
        self, incoming: Mapping[str, Any], writes: dict[Path, bytes]
    ) -> None:
        target = self.project_root / "automation/variables.json"
        current = (
            json.loads(writes[target])
            if target in writes
            else self._read_path_json(
                target,
                {
                    "version": CustomVariableStore.VERSION,
                    "global": {},
                    "metadata": {},
                },
            )
        )
        current.setdefault("global", {}).update(incoming.get("global", {}))
        current.setdefault("metadata", {}).update(incoming.get("metadata", {}))
        writes[target] = self._encode(current)

    def _merge_counter_definitions(
        self, incoming: Mapping[str, Any], writes: dict[Path, bytes]
    ) -> None:
        target = self.project_root / "counters/index.json"
        current = (
            json.loads(writes[target])
            if target in writes
            else self._read_path_json(
                target, {"version": INDEX_VERSION, "counters": []}
            )
        )
        by_id = {
            str(item.get("counter_id")): item
            for item in current.get("counters", [])
            if isinstance(item, dict)
        }
        for item in incoming.get("counters", []):
            if isinstance(item, dict):
                by_id[str(item.get("counter_id"))] = item
        writes[target] = self._encode(
            {"version": INDEX_VERSION, "counters": list(by_id.values())}
        )

    @staticmethod
    def _safe_stream_restore(
        target: Path,
        incoming: Mapping[str, Any],
        *,
        active_stream_id: str,
        reset_value: str,
    ) -> Mapping[str, Any]:
        safe = json.loads(json.dumps(incoming))
        incoming_stream = safe.get("current_stream", {})
        incoming_id = str(incoming_stream.get("stream_id", ""))
        if active_stream_id and incoming_id == active_stream_id:
            return incoming
        current: Mapping[str, Any] = {}
        try:
            if target.exists():
                current = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = {}
        empty_stream = {"stream_id": "", "value": str(reset_value)}
        current_stream = current.get("current_stream", {})
        safe["current_stream"] = (
            current_stream
            if active_stream_id
            and isinstance(current_stream, dict)
            and str(current_stream.get("stream_id", "")) == active_stream_id
            else empty_stream
        )
        current_viewers = current.get("viewers", {})
        for user_id, viewer in safe.get("viewers", {}).items():
            current_viewer = (
                current_viewers.get(user_id, {})
                if isinstance(current_viewers, dict)
                else {}
            )
            if isinstance(viewer, dict) and isinstance(current_viewer, dict):
                current_viewer_stream = current_viewer.get(
                    "current_stream", {}
                )
                viewer["current_stream"] = (
                    current_viewer_stream
                    if active_stream_id
                    and isinstance(current_viewer_stream, dict)
                    and str(current_viewer_stream.get("stream_id", ""))
                    == active_stream_id
                    else dict(empty_stream)
                )
        return safe

    # Validation and transaction -------------------------------------

    def _read_archive(
        self, archive: Path
    ) -> tuple[dict[str, Any], dict[BackupComponent, Any]]:
        try:
            with ZipFile(archive) as source:
                names = source.namelist()
                if len(names) != len(set(names)):
                    raise BackupError("Backup archive contains duplicate entries.")
                if "manifest.json" not in names:
                    raise BackupError(
                        "This is not a Streamhouse backup: manifest is missing."
                    )
                manifest = json.loads(source.read("manifest.json"))
                self._validate_manifest(manifest)
                expected_entries = {"manifest.json"} | {
                    str(details["path"])
                    for details in manifest["components"].values()
                }
                if set(names) != expected_entries:
                    raise BackupError(
                        "Backup archive contains unexpected or unlisted entries."
                    )
                payloads: dict[BackupComponent, Any] = {}
                for name, details in manifest["components"].items():
                    component = BackupComponent(name)
                    path = str(details["path"])
                    if path not in source.namelist():
                        raise BackupError(f'Backup component "{name}" is missing.')
                    content = source.read(path)
                    if hashlib.sha256(content).hexdigest() != details["sha256"]:
                        raise BackupError(
                            f'Backup component "{name}" failed its integrity check.'
                        )
                    payload = json.loads(content)
                    self._assert_safe_payload(component, payload)
                    payloads[component] = payload
                return manifest, payloads
        except BackupError:
            raise
        except (
            BadZipFile,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            OSError,
        ) as error:
            raise BackupError(
                "The selected file is not a valid Streamhouse backup."
            ) from error

    def _validate_manifest(self, manifest: Any) -> None:
        if not isinstance(manifest, dict):
            raise BackupError("Backup manifest is malformed.")
        if manifest.get("backup_format_version") != self.FORMAT_VERSION:
            raise BackupError(
                "This backup format is not supported by this Hub version."
            )
        if manifest.get("product") != "Streamhouse Hub":
            raise BackupError("This archive is not a Streamhouse Hub backup.")
        components = manifest.get("components")
        if not isinstance(components, dict) or not components:
            raise BackupError("Backup manifest has no components.")
        included = manifest.get("included_components")
        if (
            not isinstance(included, list)
            or len(included) != len(set(included))
            or set(included) != set(components)
        ):
            raise BackupError("Backup manifest component inventory is inconsistent.")
        for name, details in components.items():
            try:
                component = BackupComponent(name)
            except ValueError as error:
                raise BackupError(f'Unknown backup component "{name}".') from error
            if not isinstance(details, dict):
                raise BackupError(f'Backup component "{name}" is malformed.')
            if details.get("schema") != _COMPONENT_SCHEMAS[component]:
                raise BackupError(
                    f'Backup component "{name}" uses an unsupported schema.'
                )
            path = str(details.get("path", ""))
            if path != f"components/{name}.json" or ".." in Path(path).parts:
                raise BackupError(f'Backup component "{name}" has an unsafe path.')
            if not re.fullmatch(
                r"[0-9a-f]{64}", str(details.get("sha256", ""))
            ):
                raise BackupError(
                    f'Backup component "{name}" has no valid checksum.'
                )
            dependencies = details.get("dependencies", [])
            if not isinstance(dependencies, list):
                raise BackupError(
                    f'Backup component "{name}" has invalid dependencies.'
                )
            for dependency in dependencies:
                try:
                    BackupComponent(dependency)
                except ValueError as error:
                    raise BackupError(
                        f'Backup component "{name}" has an unknown dependency.'
                    ) from error
                if dependency not in components:
                    raise BackupError(
                        f'Backup component "{name}" is missing dependency '
                        f'"{dependency}".'
                    )
        datetime.fromisoformat(str(manifest["created_at"]))

    def _assert_safe_payload(
        self, component: BackupComponent, payload: Any
    ) -> None:
        if component is BackupComponent.USERS:
            encoded = json.dumps(payload, sort_keys=True).casefold()
            for forbidden in (
                "daily_memory",
                "memories",
                "private_notes",
                "timeline",
                '"message"',
                '"text"',
                "evidence",
            ):
                if forbidden in encoded:
                    raise BackupError(
                        "Users backup contains private message or memory content."
                    )
        self._walk_for_secrets(payload)

    @classmethod
    def _walk_for_secrets(cls, value: Any, path: str = "") -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                key_text = str(key)
                if _SECRET_KEYS.fullmatch(key_text):
                    raise BackupError(
                        "Backup source contains an ineligible secret field at "
                        f'"{path}{key_text}".'
                    )
                cls._walk_for_secrets(item, f"{path}{key_text}.")
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                cls._walk_for_secrets(item, f"{path}{index}.")
        elif isinstance(value, str) and _SECRET_TEXT.search(value):
            location = path.rstrip(".") or "value"
            raise BackupError(
                f'Backup source contains credential-like content at "{location}".'
            )

    def _validate_staged(self, writes: Mapping[Path, bytes]) -> None:
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=self.backup_directory) as temporary:
            root = Path(temporary)
            for target, content in writes.items():
                relative = target.relative_to(self.project_root)
                staged = root / relative
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_bytes(content)
            try:
                routine_path = root / "automation/routines.json"
                routines = RoutineStore(routine_path)
                if routine_path.exists():
                    routines.load()
                queue_path = root / "automation/queues.json"
                if queue_path.exists():
                    AutomationQueueStore(queue_path).load()
                variable_path = root / "automation/variables.json"
                if variable_path.exists():
                    CustomVariableStore(variable_path).load()
                command_path = root / "twitch/commands.json"
                if command_path.exists():
                    TwitchCommandTriggerStore(command_path, routines).load()
                event_path = root / "twitch/event_triggers.json"
                if event_path.exists():
                    TwitchEventTriggerStore(event_path, routines).load()
                core_path = root / "automation/core_triggers.json"
                if core_path.exists():
                    CoreTriggerStore(core_path, routines).load()
                obs_path = root / "obs/triggers.json"
                if obs_path.exists():
                    ObsTriggerStore(obs_path, routines).load()
                channel_path = root / "twitch/channel-information.json"
                if channel_path.exists():
                    ChannelInformationStore(channel_path).load()
                users_path = root / "memory/twitch_chatters.json"
                if users_path.exists():
                    ChatterHistoryStore(users_path).load()
                settings_path = root / "config/settings.json"
                if settings_path.exists():
                    SettingsStore(settings_path).load()
                counter_index = root / "counters/index.json"
                if counter_index.exists():
                    counter_store = CounterStore(counter_index.parent)
                    counter_store.list_definitions()
                    for path in counter_index.parent.glob("*.json"):
                        if path.name != "index.json":
                            counter_store.read_data(path.stem)
            except Exception as error:
                raise BackupError(
                    f"Restored data failed current-schema validation: {error}"
                ) from error

    @staticmethod
    def _commit_transaction(
        writes: Mapping[Path, bytes], deletes: set[Path]
    ) -> None:
        targets = set(writes) | deletes
        previous = {
            target: target.read_bytes() if target.exists() else None
            for target in targets
        }
        staged: dict[Path, Path] = {}
        try:
            for target, content in writes.items():
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_suffix(target.suffix + ".restore")
                temporary.write_bytes(content)
                staged[target] = temporary
            for target, temporary in staged.items():
                os.replace(temporary, target)
            for target in deletes:
                target.unlink(missing_ok=True)
        except Exception:
            for temporary in staged.values():
                temporary.unlink(missing_ok=True)
            for target, content in previous.items():
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    rollback = target.with_suffix(target.suffix + ".rollback")
                    rollback.write_bytes(content)
                    os.replace(rollback, target)
            raise

    def _expand_restore_dependencies(
        self,
        selected: set[BackupComponent],
        manifest: Mapping[str, Any],
    ) -> set[BackupComponent]:
        changed = True
        while changed:
            changed = False
            for component in tuple(selected):
                details = manifest["components"][component.value]
                for name in details.get("dependencies", []):
                    dependency = BackupComponent(name)
                    if dependency not in selected:
                        selected.add(dependency)
                        changed = True
        return selected

    # Helpers ----------------------------------------------------------

    def _task_dependencies(
        self, tasks: Any
    ) -> tuple[set[str], set[str], set[str], set[str]]:
        routines: set[str] = set()
        queues: set[str] = set()
        counters: set[str] = set()
        custom: set[str] = set()
        if not isinstance(tasks, list):
            return routines, queues, counters, custom
        for task in tasks:
            if not isinstance(task, dict):
                continue
            task_type = str(task.get("task_type", ""))
            config = (
                task.get("config", {})
                if isinstance(task.get("config"), dict)
                else {}
            )
            if task_type in {
                "core.run_routine",
                "core.logic_while",
                "core.set_routine_state",
                "core.set_task_state",
            }:
                target = str(config.get("routine_id", "")).strip()
                if target:
                    routines.add(target)
            if task_type == "core.logic_random_choice":
                routines.update(
                    str(item.get("routine_id", "")).strip()
                    for item in config.get("choices", [])
                    if isinstance(item, dict)
                    and str(item.get("routine_id", "")).strip()
                )
            if task_type == "core.logic_switch":
                cases = config.get("cases", {})
                if isinstance(cases, dict):
                    routines.update(
                        str(value).strip()
                        for value in cases.values()
                        if str(value).strip()
                    )
                default = str(config.get("default_routine_id", "")).strip()
                if default:
                    routines.add(default)
            if task_type in {"core.set_queue_state", "core.clear_queue"}:
                queue_id = str(config.get("queue_id", "")).strip()
                if queue_id:
                    queues.add(queue_id)
            if task_type.startswith("counter."):
                counter_id = str(config.get("counter_id", "")).strip()
                if counter_id:
                    counters.add(counter_id)
            custom.update(
                _CUSTOM_PLACEHOLDER.findall(
                    json.dumps(task, ensure_ascii=False)
                )
            )
            nested = []
            if isinstance(task.get("then_tasks"), list):
                nested.extend(task["then_tasks"])
            if isinstance(task.get("else_tasks"), list):
                nested.extend(task["else_tasks"])
            dependencies = self._task_dependencies(nested)
            routines.update(dependencies[0])
            queues.update(dependencies[1])
            counters.update(dependencies[2])
            custom.update(dependencies[3])
        return routines, queues, counters, custom

    def _json_or_default(self, relative: str, default: Any) -> Any:
        return self._read_path_json(self.project_root / relative, default)

    @staticmethod
    def _read_path_json(path: Path, default: Any) -> Any:
        if not path.exists():
            return json.loads(json.dumps(default))
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise BackupError(f'Backup source "{path.name}" is unreadable.') from error

    @staticmethod
    def _expect_schema(
        payload: Any, expected: int, label: str, key: str
    ) -> None:
        if not isinstance(payload, dict) or payload.get(key) != expected:
            raise BackupError(f"{label} does not use the current schema.")

    @staticmethod
    def _encode(payload: Any) -> bytes:
        return (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")

    def _directory_for(self, backup_type: str | None) -> Path:
        value = self._normalize_type(backup_type or "manual")
        return {
            "manual": self.manual_directory,
            "automatic": self.automatic_directory,
            "safety": self.safety_directory,
        }[value]

    @staticmethod
    def _normalize_type(value: str) -> str:
        clean = str(value).strip().casefold().replace("before-restore", "safety")
        if clean not in {"manual", "automatic", "safety"}:
            raise BackupError("Backup type must be manual, automatic, or safety.")
        return clean

    def _rotate_automatic(self) -> None:
        archives = sorted(
            self.automatic_directory.glob(f"*{self.EXTENSION}"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for obsolete in archives[self.AUTOMATIC_RETENTION :]:
            obsolete.unlink(missing_ok=True)

    def _rewrite_archive(
        self,
        archive: Path,
        manifest: dict[str, Any],
        payloads: Mapping[BackupComponent, Any],
    ) -> None:
        encoded = {
            component: self._encode(payload)
            for component, payload in payloads.items()
        }
        for component, content in encoded.items():
            manifest["components"][component.value]["sha256"] = hashlib.sha256(
                content
            ).hexdigest()
        manifest["content_digest"] = hashlib.sha256(
            b"".join(
                component.value.encode() + encoded[component]
                for component in sorted(encoded, key=lambda item: item.value)
            )
        ).hexdigest()
        temporary = archive.with_suffix(archive.suffix + ".tmp")
        try:
            with ZipFile(temporary, "w", ZIP_DEFLATED) as target:
                target.writestr("manifest.json", self._encode(manifest))
                for component, content in encoded.items():
                    target.writestr(
                        f"components/{component.value}.json", content
                    )
            os.replace(temporary, archive)
        finally:
            temporary.unlink(missing_ok=True)
