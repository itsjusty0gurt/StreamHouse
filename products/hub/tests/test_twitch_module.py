from __future__ import annotations

from pathlib import Path

import pytest

from products.hub.automation.models import (
    TaskDefinition,
    TaskExecutionResult,
    TriggerEvent,
)
from products.hub.automation.routines import RoutineStore
from products.hub.automation.task_catalog import CORE_TASK_METADATA
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.modules import (
    ActionRegistry,
    ModuleContext,
    ModuleEnablementStore,
    ModuleLifecycle,
    ModuleRegistry,
    TriggerProviderRegistry,
)
from products.hub.modules.bootstrap import CORE_MODULE_CAPABILITIES
from products.hub.modules.bundled.twitch import TWITCH_MODULE_ID, TwitchModule
from products.hub.twitch.automation_triggers import TwitchEventTriggerStore
from products.hub.twitch.tasks import TWITCH_TASK_LABELS


class _UnexpectedTwitchHandler:
    task_type = "twitch.send_chat_message"

    def execute(
        self,
        task: TaskDefinition,
        trigger: TriggerEvent,
    ) -> TaskExecutionResult:
        raise AssertionError("disabled Twitch task handler must not execute")


def _registry(path: Path, module: TwitchModule, *, enabled: bool) -> ModuleRegistry:
    store = ModuleEnablementStore(path)
    store.set_enabled(TWITCH_MODULE_ID, enabled)
    registry = ModuleRegistry(
        core_version="0.3.0-alpha",
        core_capabilities=CORE_MODULE_CAPABILITIES,
        enablement_store=store,
    )
    registry.register(module)
    return registry


def _contracts(root: Path):
    routines = RoutineStore(root / "automation" / "routines.json")
    trigger_store = TwitchEventTriggerStore(
        root / "twitch" / "event_triggers.json",
        routines,
    )
    tasks = TaskRegistry(CORE_TASK_METADATA)
    tasks.register(_UnexpectedTwitchHandler())
    variables = VariableRegistry()
    providers = TriggerProviderRegistry()
    context = ModuleContext(
        core_version="0.3.0-alpha",
        events=object(),
        variables=variables,
        tasks=tasks,
        actions=ActionRegistry(),
        routine_store=routines,
        automation=object(),
        trigger_providers=providers,
        trigger_stores={"twitch.automation": trigger_store},
        data_root=root,
    )
    return context, tasks, variables, providers, trigger_store, routines


def test_disabled_twitch_module_preserves_static_catalogs_without_runtime(
    tmp_path: Path,
) -> None:
    module = TwitchModule()
    registry = _registry(tmp_path / "modules.json", module, enabled=False)
    context, tasks, variables, providers, trigger_store, routines = _contracts(
        tmp_path
    )
    routine = routines.add("Persisted Twitch automation")
    trigger = trigger_store.add(routine.routine_id, "channel.follow")
    trigger_store.save()

    registry.register_catalogs(lambda _descriptor: context)
    registry.register_enabled(lambda _descriptor: context)
    registry.configure_enabled()
    registry.start_enabled()

    status = registry.status(TWITCH_MODULE_ID)
    assert status is not None
    assert status.lifecycle is ModuleLifecycle.DISABLED
    assert set(TWITCH_TASK_LABELS).issubset(
        {item.task_type for item in tasks.visible_metadata()}
    )
    assert tasks.availability("twitch.send_chat_message") == (
        False,
        "The Twitch module is disabled or unavailable.",
    )
    result = tasks.execute(
        TaskDefinition(
            "task-1",
            "twitch.send_chat_message",
            "Send message",
            {"message": "Hello"},
        ),
        TriggerEvent("trigger-1", "test", "manual", {}),
    )
    assert result.succeeded is True and result.skipped is True

    provider = providers.get("twitch.automation")
    assert provider is not None
    assert provider.store is trigger_store
    assert provider.current_availability().available is False
    assert trigger_store.get(trigger.trigger_id) is not None

    contextual = variables.resolve(
        "user.display_name",
        {"user.display_name": "TestViewer"},
    )
    assert contextual is not None
    assert contextual.available is False
    assert "disabled or unavailable" in contextual.detail
    assert not hasattr(module, "service")
    assert not hasattr(module, "auth")


def test_enabled_twitch_module_registers_static_catalog_exactly_once(
    tmp_path: Path,
) -> None:
    module = TwitchModule()
    registry = _registry(tmp_path / "modules.json", module, enabled=True)
    context, tasks, variables, providers, _trigger_store, _routines = _contracts(
        tmp_path
    )

    registry.register_catalogs(lambda _descriptor: context)
    registry.register_enabled(lambda _descriptor: context)
    registry.configure_enabled()
    registry.start_enabled()

    status = registry.status(TWITCH_MODULE_ID)
    assert status is not None
    assert status.lifecycle is ModuleLifecycle.STARTED
    assert tasks.metadata("twitch.get_user_clip") is not None
    assert tasks.availability("twitch.get_user_clip") == (True, "")
    assert providers.get("twitch.automation") is not None
    contextual = variables.resolve(
        "user.display_name",
        {"user.display_name": "TestViewer"},
    )
    assert contextual is not None and contextual.available is True
    assert contextual.value == "TestViewer"

    with pytest.raises(ValueError, match="already registered"):
        module.register_catalog(context)
