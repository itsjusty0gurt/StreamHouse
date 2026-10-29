from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Mapping

from PySide6.QtCore import QTimer

from products.hub.automation.models import TriggerEvent
from products.hub.modules.actions import (
    ActionDescriptor,
    ActionExecutionContext,
    ActionParameter,
    ActionResult,
)
from products.hub.modules.contracts import Availability, ModuleContext, ModuleDescriptor
from products.hub.modules.contributions import (
    BackupContributor,
    ModuleTabContribution,
    TriggerProviderContribution,
)
from products.hub.modules.bundled.obs.variables import ObsVariableProvider
from products.hub.obs_service.config import ObsConfigStore, ObsConnectionConfig
from products.hub.obs_service.models import ObsEvent, ObsRequestResult
from products.hub.obs_service.service import ObsWebSocketService
from products.hub.obs_service.tasks import OBS_TASK_LABELS, register_obs_tasks
from products.hub.obs_service.triggers import OBS_TRIGGER_TYPES, ObsTriggerStore
from products.hub.core.events import Events
from shared.streamhouse_runtime.json_store import json_store_exists, load_validated_json
from shared.streamhouse_runtime.logger import Logger


OBS_MODULE_ID = "streamhouse.obs"
OBS_TRIGGER_PROVIDER_ID = "obs.automation"
OBS_BACKUP_CONTRIBUTOR_ID = "obs.configuration"
OBS_TRIGGERS_BACKUP_CONTRIBUTOR_ID = "obs.triggers"


class ObsModule:
    descriptor = ModuleDescriptor(
        module_id=OBS_MODULE_ID,
        display_name="OBS",
        version="0.3.0-alpha",
        category="Broadcasting",
        enabled_by_default=True,
        required_core_capabilities=frozenset(
            {"actions", "automation", "backup", "tabs", "tasks", "triggers", "variables"}
        ),
        capabilities=frozenset(
            {"obs.actions", "obs.automation", "obs.connection", "obs.ui", "obs.variables"}
        ),
        ui_label="OBS",
    )

    def __init__(self) -> None:
        self.service = ObsWebSocketService()
        self.config_store: ObsConfigStore | None = None
        self.trigger_store: ObsTriggerStore | None = None
        self._config = ObsConnectionConfig()
        self._password = ""
        self._context: ModuleContext | None = None
        self._runtime_available = False
        self._events_subscribed = False

    def register_catalog(self, context: ModuleContext) -> None:
        self._context = context
        data_root = Path(context.data_root) if context.data_root is not None else None
        routine_store = context.routine_store
        if data_root is None or routine_store is None:
            raise ValueError("OBS module requires Core data-root and routine-store contracts.")
        self.config_store = ObsConfigStore(data_root / "obs" / "connection.json")
        self.trigger_store = ObsTriggerStore(
            data_root / "obs" / "triggers.json",
            routine_store,
        )
        try:
            self.trigger_store.load()
        except (OSError, TypeError, ValueError) as error:
            self.trigger_store.triggers = []
            Logger.warning(f"Could not load OBS automation triggers: {error}", source="OBS")

        register_obs_tasks(context.tasks, self.service, context.actions)
        context.tasks.register_provider_availability(
            OBS_TASK_LABELS,
            self.task_availability,
        )
        context.variables.register(
            ObsVariableProvider(
                available=lambda: self._runtime_available,
                connected=lambda: self.service.connected,
                scene=lambda: self.service.current_program_scene,
            )
        )
        if context.trigger_providers is not None:
            context.trigger_providers.register(
                TriggerProviderContribution(
                    provider_id=OBS_TRIGGER_PROVIDER_ID,
                    owner_module_id=OBS_MODULE_ID,
                    display_name="OBS",
                    store=self.trigger_store,
                    event_types=OBS_TRIGGER_TYPES,
                    editor_service=self.service,
                    availability=self.availability,
                )
            )
        if context.backup is not None:
            context.backup.register(
                BackupContributor(
                    contributor_id=OBS_BACKUP_CONTRIBUTOR_ID,
                    owner_module_id=OBS_MODULE_ID,
                    display_name="OBS Hub Configuration",
                    schema_version=1,
                    export=self._backup_export,
                    validate=self._backup_validate,
                )
            )
            context.backup.register(
                BackupContributor(
                    contributor_id=OBS_TRIGGERS_BACKUP_CONTRIBUTOR_ID,
                    owner_module_id=OBS_MODULE_ID,
                    display_name="OBS Automation Triggers",
                    schema_version=ObsTriggerStore.VERSION,
                    export=self._backup_triggers_export,
                    validate=self._backup_triggers_validate,
                )
            )

    def register(self, context: ModuleContext) -> None:
        self._context = context
        self._register_actions(context.actions)
        if context.tabs is not None:
            context.tabs.register(
                ModuleTabContribution(
                    tab_id="obs.workspace",
                    owner_module_id=OBS_MODULE_ID,
                    label="OBS",
                    order=350,
                    factory=self._create_page,
                )
            )

    def configure(self, _context: ModuleContext) -> None:
        if self.config_store is None:
            raise RuntimeError("OBS module catalog was not registered.")
        self._config, self._password = self.config_store.load()
        self.service.configure(
            self._config.host,
            self._config.port,
            self._password,
            auto_reconnect=True,
        )
        self.service.set_default_audio_input(self._config.default_mute_input)

    def start(self) -> None:
        if self._events_subscribed:
            return
        Events.subscribe("obs_event", self._handle_event)
        self.service.state_changed.connect(self._state_changed)
        self._events_subscribed = True
        self._runtime_available = True
        if self._config.auto_connect:
            QTimer.singleShot(250, self.service, self.service.connect)

    def stop(self) -> None:
        self._runtime_available = False
        if self._events_subscribed:
            Events.unsubscribe("obs_event", self._handle_event)
            try:
                self.service.state_changed.disconnect(self._state_changed)
            except RuntimeError:
                pass
            self._events_subscribed = False
        self.service.cancel_pending_requests("OBS module is shutting down.")
        self.service.disconnect()

    def availability(self) -> Availability:
        return Availability(
            self._runtime_available,
            "" if self._runtime_available else "The OBS module is disabled or unavailable.",
        )

    def task_availability(self) -> tuple[bool, str]:
        current = self.availability()
        return current.available, current.detail

    def connection_values(self) -> tuple[ObsConnectionConfig, str]:
        return self._config, self._password

    def save_connection(self, config: ObsConnectionConfig, password: str) -> None:
        if self.config_store is None:
            raise RuntimeError("OBS configuration is unavailable.")
        self.config_store.save(config, password)
        self._config = config
        self._password = password
        self.service.configure(
            config.host,
            config.port,
            password,
            auto_reconnect=True,
        )
        self.service.set_default_audio_input(config.default_mute_input)

    def _create_page(self, parent: object) -> object:
        from products.hub.modules.bundled.obs.page import ObsModulePage

        return ObsModulePage(self, parent)  # type: ignore[arg-type]

    def _state_changed(self, state: object, _detail: str) -> None:
        context = self._context
        if context is not None and context.integration_status_changed is not None:
            context.integration_status_changed("obs", state)

    def _handle_event(self, obs_event: ObsEvent) -> None:
        if not self._runtime_available or self.trigger_store is None:
            return
        context = self._context
        if context is None or context.automation is None:
            return
        for trigger in self.trigger_store.evaluate(obs_event):
            trigger_context = dict(trigger.context)
            if context.trigger_context_enricher is not None:
                for key, value in context.trigger_context_enricher().items():
                    if trigger_context.get(key, "--") in {"", "--"}:
                        trigger_context[key] = value
            normalized = TriggerEvent(
                trigger_id=trigger.trigger_id,
                service=trigger.service,
                trigger_type=trigger.trigger_type,
                context=trigger_context,
                event_id=trigger.event_id,
                occurred_at=trigger.occurred_at,
            )
            execution = context.automation.publish_trigger(normalized)
            if context.execution_recorder is not None:
                context.execution_recorder(execution, f"OBS {obs_event.event_type}")
            if execution.succeeded:
                Logger.info(
                    f'Executed OBS automation for "{obs_event.event_type}".',
                    source="AUTOMATION",
                )
            elif execution.handled:
                Logger.warning(
                    f'OBS automation failed for "{obs_event.event_type}".',
                    source="AUTOMATION",
                )

    def _register_actions(self, registry) -> None:
        definitions = (
            ("obs.stream.start", "Start Streaming", "StartStream", ()),
            ("obs.stream.stop", "Stop Streaming", "StopStream", ()),
            ("obs.record.start", "Start Recording", "StartRecord", ()),
            ("obs.record.stop", "Stop Recording", "StopRecord", ()),
            (
                "obs.scene.switch",
                "Switch Scene",
                "SetCurrentProgramScene",
                (ActionParameter("scene", "Scene", "text", variable_aware=True),),
            ),
            (
                "obs.input.mute",
                "Mute Input",
                "SetInputMute",
                (ActionParameter("input", "Input", "text", variable_aware=True),),
            ),
            (
                "obs.input.unmute",
                "Unmute Input",
                "SetInputMute",
                (ActionParameter("input", "Input", "text", variable_aware=True),),
            ),
        )
        for action_id, label, request_type, parameters in definitions:
            registry.register(
                ActionDescriptor(
                    action_id=action_id,
                    display_name=label,
                    owner_module_id=OBS_MODULE_ID,
                    category="OBS",
                    capability="obs.connection",
                    parameters=parameters,
                    availability=lambda: Availability(
                        self._runtime_available and self.service.connected,
                        "OBS is not connected.",
                    ),
                    handler=self._action_handler(action_id, request_type),
                )
            )

    def _action_handler(self, action_id: str, request_type: str):
        def invoke(
            parameters: Mapping[str, object],
            _execution: ActionExecutionContext,
        ) -> ActionResult:
            request_data: dict[str, object] = {}
            if action_id == "obs.scene.switch":
                request_data["sceneName"] = str(parameters["scene"])
            elif action_id in {"obs.input.mute", "obs.input.unmute"}:
                request_data = {
                    "inputName": str(parameters["input"]),
                    "inputMuted": action_id.endswith(".mute"),
                }
            return self._action_result(self.service.request_and_wait(request_type, request_data))

        return invoke

    @staticmethod
    def _action_result(result: ObsRequestResult) -> ActionResult:
        return ActionResult(
            result.succeeded,
            (
                f"OBS request {result.request_type} completed."
                if result.succeeded
                else f"OBS request {result.request_type or 'unknown'} failed: "
                f"{result.comment.strip() or result.code}"
            ),
        )

    def _backup_export(self, project_root: object) -> Mapping[str, object]:
        path = Path(project_root) / "obs" / "connection.json"
        config = (
            load_validated_json(path, ObsConfigStore._parse_payload)
            if json_store_exists(path)
            else ObsConnectionConfig()
        )
        return {"version": 1, **asdict(config)}

    @staticmethod
    def _backup_validate(payload: object) -> None:
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("OBS configuration does not use the current schema.")
        ObsConnectionConfig.from_dict(payload).validate()

    def _backup_triggers_export(
        self,
        project_root: object,
    ) -> Mapping[str, object]:
        assert self.trigger_store is not None
        path = Path(project_root) / "obs" / "triggers.json"
        payload: object = (
            json.loads(path.read_text(encoding="utf-8"))
            if json_store_exists(path)
            else {"version": ObsTriggerStore.VERSION, "triggers": []}
        )
        self._backup_triggers_validate(payload)
        assert isinstance(payload, Mapping)
        return payload

    def _backup_triggers_validate(self, payload: object) -> None:
        assert self.trigger_store is not None
        self.trigger_store._parse_payload(payload)
