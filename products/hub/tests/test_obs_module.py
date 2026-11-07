from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication

from products.hub.automation.core_tasks import WaitTask
from products.hub.automation.models import TaskDefinition, TriggerEvent
from products.hub.automation.routines import RoutineStore
from products.hub.automation.service import AutomationService
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.core.events import Events
from products.hub.modules import (
    ActionRegistry,
    BackupContributorRegistry,
    ModuleContext,
    ModuleEnablementStore,
    ModuleLifecycle,
    ModuleRegistry,
    ModuleTabRegistry,
    TriggerProviderRegistry,
)
from products.hub.modules.bootstrap import CORE_MODULE_CAPABILITIES
from products.hub.modules.bundled.obs import OBS_MODULE_ID, ObsModule
from products.hub.obs_service.config import ObsConnectionConfig
from products.hub.obs_service.models import ObsConnectionState, ObsEvent, ObsRequestResult


def _registry(path: Path, module: ObsModule, *, enabled: bool = True) -> ModuleRegistry:
    store = ModuleEnablementStore(path)
    store.set_enabled(OBS_MODULE_ID, enabled)
    registry = ModuleRegistry(
        core_version="0.3.0-alpha",
        core_capabilities=CORE_MODULE_CAPABILITIES,
        enablement_store=store,
    )
    registry.register(module)
    return registry


def _contracts(root: Path) -> tuple[ModuleContext, TaskRegistry, VariableRegistry]:
    tasks = TaskRegistry()
    variables = VariableRegistry()
    context = ModuleContext(
        core_version="0.3.0-alpha",
        events=object(),
        variables=variables,
        tasks=tasks,
        actions=ActionRegistry(),
        routine_store=RoutineStore(root / "automation" / "routines.json"),
        automation=object(),
        trigger_providers=TriggerProviderRegistry(),
        tabs=ModuleTabRegistry(),
        backup=BackupContributorRegistry(),
        data_root=root,
    )
    return context, tasks, variables


def test_obs_module_registers_catalog_actions_tab_and_lifecycle(tmp_path: Path) -> None:
    module = ObsModule()
    module.service.configure = Mock()
    module.service.set_default_audio_input = Mock()
    module.service.cancel_pending_requests = Mock()
    module.service.disconnect = Mock()
    module.service.request_and_wait = Mock(
        return_value=ObsRequestResult(
            "request-1", "SetCurrentProgramScene", True, 100
        )
    )
    registry = _registry(tmp_path / "modules.json", module)
    context, tasks, variables = _contracts(tmp_path)
    assert module.config_store is None

    registry.register_catalogs(lambda _descriptor: context)
    assert "obs.set_program_scene" in tasks.registered_types()
    assert variables.definition("obs.current_scene") is not None
    assert variables.definition("hub.connected_to_obs") is not None
    assert context.trigger_providers.get("obs.automation") is not None
    assert context.backup.get("obs.configuration") is not None
    trigger_backup = context.backup.get("obs.triggers")
    assert trigger_backup is not None
    assert trigger_backup.export(tmp_path) == {"version": 1, "triggers": []}

    module.config_store.load = Mock(  # type: ignore[union-attr,method-assign]
        return_value=(ObsConnectionConfig(host="obs.local", port=4456), "protected")
    )
    registry.register_enabled(lambda _descriptor: context)
    registry.configure_enabled()
    registry.start_enabled()

    assert registry.status(OBS_MODULE_ID).lifecycle is ModuleLifecycle.STARTED  # type: ignore[union-attr]
    assert context.actions.get("obs.scene.switch") is not None
    assert [item.tab_id for item in context.tabs.contributions()] == ["obs.workspace"]
    module.service.configure.assert_called_once_with(
        "obs.local", 4456, "protected", auto_reconnect=True
    )
    module.service.state = ObsConnectionState.CONNECTED
    module.service._identified = True
    task_result = tasks.execute(
        TaskDefinition(
            "task-scene",
            "obs.set_program_scene",
            "Change scene",
            {"scene": "Gameplay"},
        ),
        TriggerEvent("trigger-1", "test", "manual", {}),
    )
    assert task_result.succeeded is True
    module.service.request_and_wait.assert_called_once_with(
        "SetCurrentProgramScene", {"sceneName": "Gameplay"}
    )

    registry.stop_started()
    module.service.cancel_pending_requests.assert_called_once()
    module.service.disconnect.assert_called_once()


def test_disabled_obs_module_preserves_catalog_and_skips_tasks(tmp_path: Path) -> None:
    module = ObsModule()
    registry = _registry(tmp_path / "modules.json", module, enabled=False)
    context, tasks, variables = _contracts(tmp_path)

    registry.register_catalogs(lambda _descriptor: context)
    registry.register_enabled(lambda _descriptor: context)
    registry.configure_enabled()
    registry.start_enabled()

    status = registry.status(OBS_MODULE_ID)
    assert status is not None
    assert status.lifecycle is ModuleLifecycle.DISABLED
    assert context.actions.descriptors() == ()
    assert context.tabs.contributions() == ()
    assert context.trigger_providers.get("obs.automation") is not None
    trigger_backup = context.backup.get("obs.triggers")
    assert trigger_backup is not None
    assert trigger_backup.export(tmp_path) == {"version": 1, "triggers": []}
    assert context.trigger_providers.get("obs.automation").current_availability().available is False  # type: ignore[union-attr]
    scene = variables.resolve("obs.current_scene", {})
    assert scene is not None and scene.available is False

    result = tasks.execute(
        TaskDefinition("task-1", "obs.set_program_scene", "Scene", {"scene": "Main"}),
        TriggerEvent("trigger-1", "test", "manual", {}),
    )
    assert result.succeeded is True
    assert result.skipped is True
    assert "disabled or unavailable" in result.detail


def test_obs_action_ids_reject_duplicate_registration(tmp_path: Path) -> None:
    first = ObsModule()
    second = ObsModule()
    context, _tasks, _variables = _contracts(tmp_path)
    first.register_catalog(context)
    first.register(context)

    second._context = context
    try:
        second._register_actions(context.actions)
    except ValueError as error:
        assert "already registered" in str(error)
    else:
        raise AssertionError("duplicate OBS actions must be rejected")


@pytest.mark.parametrize(
    ("event_type", "filters", "matching_data", "nonmatching_data"),
    (
        (
            "CurrentProgramSceneChanged",
            {"sceneName": "Gameplay"},
            {"sceneName": "Gameplay"},
            {"sceneName": "Intermission"},
        ),
        (
            "InputMuteStateChanged",
            {"inputName": "Mic/Aux"},
            {"inputName": "Mic/Aux", "inputMuted": True},
            {"inputName": "Desktop Audio", "inputMuted": True},
        ),
    ),
)
def test_obs_event_automation_runs_after_websocket_event_callback(
    tmp_path: Path,
    event_type: str,
    filters: dict[str, str],
    matching_data: dict[str, object],
    nonmatching_data: dict[str, object],
) -> None:
    application = QCoreApplication.instance() or QCoreApplication([])
    module = ObsModule()
    tasks = TaskRegistry()
    variables = VariableRegistry()
    routines = RoutineStore(tmp_path / "automation" / "routines.json")
    automation = AutomationService(routines, tasks)
    context = ModuleContext(
        core_version="0.3.0-alpha",
        events=object(),
        variables=variables,
        tasks=tasks,
        actions=ActionRegistry(),
        routine_store=routines,
        automation=automation,
        trigger_providers=TriggerProviderRegistry(),
        tabs=ModuleTabRegistry(),
        backup=BackupContributorRegistry(),
        data_root=tmp_path,
    )
    tasks.register(WaitTask())
    module.register_catalog(context)

    routine = routines.add("Preview the destination scene")
    routines.add_task(
        routine.routine_id,
        task_type="core.wait",
        name="Wait",
        config={"duration": "1", "unit": "milliseconds"},
    )
    routines.add_task(
        routine.routine_id,
        task_type="obs.set_preview_scene",
        name="Preview destination",
        config={"scene": "Gameplay"},
    )
    module.trigger_store.add(  # type: ignore[union-attr]
        routine.routine_id,
        event_type,
        filters=filters,
    )

    callback_active = True
    request_phases: list[str] = []

    def request_and_wait(
        request_type: str,
        request_data: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> ObsRequestResult:
        request_phases.append("inside" if callback_active else "after")
        return ObsRequestResult(
            "request-1",
            request_type,
            True,
            100,
            response_data=dict(request_data or {}),
        )

    module.service.request_and_wait = Mock(side_effect=request_and_wait)  # type: ignore[method-assign]
    synchronous_consumer = Mock()
    module.start()
    Events.subscribe("obs_event", synchronous_consumer)
    try:
        module.service._publish_event(  # noqa: SLF001 - exercise the WebSocket event boundary
            event_type,
            matching_data,
        )
        assert synchronous_consumer.call_count == 1
        assert request_phases == []

        callback_active = False
        application.processEvents()
        assert request_phases == ["after"]

        module.service._publish_event(  # noqa: SLF001 - verify destination filtering
            event_type,
            nonmatching_data,
        )
        application.processEvents()
        assert request_phases == ["after"]
    finally:
        Events.unsubscribe("obs_event", synchronous_consumer)
        module.stop()
