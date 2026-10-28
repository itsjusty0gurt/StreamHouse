from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

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
    AutomationSubpageRegistry,
    BackupContributorRegistry,
    ModuleContext,
    ModuleEnablementStore,
    ModuleLifecycle,
    ModuleRegistry,
    ModuleTabRegistry,
    TriggerProviderRegistry,
)
from products.hub.modules.bootstrap import CORE_MODULE_CAPABILITIES
from products.hub.modules.bundled.twitch import TWITCH_MODULE_ID, TwitchModule
from products.hub.twitch.automation_triggers import TwitchEventTriggerStore
from products.hub.twitch.models import TwitchMessage
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
        data_root=root,
    )
    return context, tasks, variables, providers, trigger_store, routines


def test_disabled_twitch_module_preserves_static_catalogs_without_runtime(
    tmp_path: Path,
) -> None:
    module = TwitchModule()
    module.auth.restore = Mock()
    module.bot_auth.restore = Mock()
    registry = _registry(tmp_path / "modules.json", module, enabled=False)
    context, tasks, variables, providers, trigger_store, routines = _contracts(
        tmp_path
    )
    module.bind_trigger_store(trigger_store)
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
    assert module.started is False
    assert module.maintenance_timer is None
    module.auth.restore.assert_not_called()
    module.bot_auth.restore.assert_not_called()


def test_enabled_twitch_module_registers_static_catalog_exactly_once(
    tmp_path: Path,
) -> None:
    module = TwitchModule()
    module.auth.restore = Mock()
    module.bot_auth.restore = Mock()
    module.service.disconnect = Mock(return_value=True)
    registry = _registry(tmp_path / "modules.json", module, enabled=True)
    context, tasks, variables, providers, _trigger_store, _routines = _contracts(
        tmp_path
    )
    module.bind_trigger_store(_trigger_store)

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
    assert module.started is True
    module.auth.restore.assert_called_once_with()
    module.bot_auth.restore.assert_called_once_with()

    with pytest.raises(ValueError, match="already registered"):
        module.register_catalog(context)

    registry.stop_started()
    registry.stop_started()
    module.service.disconnect.assert_called_once_with()
    assert module.started is False


def test_twitch_runtime_start_stop_are_single_owned_and_late_events_are_ignored(
    tmp_path: Path,
) -> None:
    module = TwitchModule()
    module.auth.restore = Mock()
    module.bot_auth.restore = Mock()
    module.auth.shutdown = Mock()
    module.bot_auth.shutdown = Mock()
    module.service.disconnect = Mock(return_value=True)
    registry = _registry(tmp_path / "modules.json", module, enabled=True)
    context, _tasks, _variables, _providers, trigger_store, _routines = _contracts(
        tmp_path
    )
    module.bind_trigger_store(trigger_store)
    trigger_store.evaluate_first_message = Mock(return_value=())

    registry.register_catalogs(lambda _descriptor: context)
    registry.register_enabled(lambda _descriptor: context)
    registry.configure_enabled()
    registry.start_enabled()
    registry.start_enabled()

    module.auth.restore.assert_called_once_with()
    module.bot_auth.restore.assert_called_once_with()

    registry.stop_started()
    registry.stop_started()
    module.publish_first_message(
        TwitchMessage(
            username="Viewer",
            text="hello",
            received_at=datetime.now(timezone.utc),
        ),
        stream_is_live=True,
    )

    module.service.disconnect.assert_called_once_with()
    module.auth.shutdown.assert_called_once_with()
    module.bot_auth.shutdown.assert_called_once_with()
    trigger_store.evaluate_first_message.assert_not_called()


def test_twitch_module_preserves_published_credential_paths() -> None:
    module = TwitchModule()

    assert module.auth.store.path.name == "twitch-token.dat"
    assert module.bot_auth.store.path.name == "twitch-bot-token.dat"


@pytest.mark.parametrize("enabled", [False, True])
def test_twitch_ui_and_backup_contributions_follow_module_availability(
    tmp_path: Path,
    enabled: bool,
) -> None:
    module = TwitchModule()
    module.auth.restore = Mock()
    module.bot_auth.restore = Mock()
    module.bind_ui_factories(
        workspace=lambda parent: ("workspace", parent),
        commands=lambda parent: ("commands", parent),
    )
    registry = _registry(tmp_path / "modules.json", module, enabled=enabled)
    context, tasks, variables, providers, trigger_store, routines = _contracts(
        tmp_path
    )
    module.bind_trigger_store(trigger_store)
    tabs = ModuleTabRegistry()
    automation_pages = AutomationSubpageRegistry()
    backup = BackupContributorRegistry()
    context = ModuleContext(
        core_version=context.core_version,
        events=context.events,
        variables=variables,
        tasks=tasks,
        actions=context.actions,
        routine_store=routines,
        automation=context.automation,
        trigger_providers=providers,
        tabs=tabs,
        automation_pages=automation_pages,
        backup=backup,
        data_root=tmp_path,
    )

    registry.register_catalogs(lambda _descriptor: context)
    registry.register_enabled(lambda _descriptor: context)

    assert [item.page_id for item in automation_pages.contributions()] == [
        "twitch.commands"
    ]
    assert automation_pages.contributions()[0].current_availability().available is enabled
    assert [item.tab_id for item in tabs.contributions()] == (
        ["twitch.workspace"] if enabled else []
    )
    assert {item.contributor_id for item in backup.contributions()} == {
        "twitch.channel_information",
        "twitch.commands",
        "twitch.triggers",
        "twitch.users",
    }
    assert module.command_store is not None
    assert module.command_store.path == tmp_path / "twitch" / "commands.json"
    assert module.channel_information_store is not None
    assert module.channel_information_store.path == (
        tmp_path / "twitch" / "channel-information.json"
    )
